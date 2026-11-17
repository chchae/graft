"""Fixed-frame core validation; no docking calculations in these tests."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, glide_pose_selection as selection


class Atoms:
    def __init__(self, xyz):
        self.values = [SimpleNamespace(atomic_number=6, x=x, y=y, z=z) for x,y,z in xyz]
    def __iter__(self):
        return iter(self.values)
    def __getitem__(self, i):
        if i < 1: raise IndexError(i)
        return self.values[i-1]


class Pose:
    def __init__(self, xyz, score=-8, matches=None):
        self.atom = Atoms(xyz)
        self.matches = [[1,2]] if matches is None else matches
        self.property = {'r_i_docking_score':score}
        self.title = 'CMPID_1'
    def copy(self):
        return copy.deepcopy(self)


def matcher(st, smarts, unique=False):
    assert unique is False  # Retain symmetry permutations, not just atom sets.
    return st.matches


def validator():
    ref = Pose([(0,0,0),(2,0,0)])
    return selection.CoreValidator(ref, 'CC', [1,2], matcher=matcher)


def test_new_glide_inputs_explicitly_skip_core_nonmatches(tmp_path):
    text=_core.write_constrained_glide_input(tmp_path,Path('grid'),Path('lig'),Path('ref'),'CC','1,2').read_text()
    assert 'CORE_FILTER True' in text
    assert 'CORECONS_FALLBACK False' in text
    assert 'CORE_POS_MAX_RMSD 1.0' in text


@pytest.mark.parametrize('distance,accepted', [(0,True),(1,True),(1.00001,False),(10,False)])
def test_fixed_frame_rmsd_without_realigning_the_ligand(distance, accepted):
    p=Pose([(distance,0,0),(2+distance,0,0)])
    selector=selection.PoseSelector(core=validator())
    selector.add(p,1)
    assert bool(selector.poses) is accepted
    if accepted:assert selector.poses[1].property['r_graft_core_rmsd']==pytest.approx(distance)


def test_reject_before_contact_and_score_ranking_but_keep_good_other_state():
    s=selection.PoseSelector([10,0,0],core=validator())
    s.add(Pose([(10,0,0),(12,0,0)],score=-50),1)
    s.add(Pose([(0,0,0),(2,0,0)],score=-7),1)
    s.add(Pose([(0,0,0),(2,0,0)],score=-100,matches=[]),1)
    assert s.scores=={1:-7}
    assert s.core_rejections[1]=={'core_rmsd_exceeded':1,'no_core_match':1}


def test_symmetry_permutations_are_considered_against_frozen_reference_mapping():
    p=Pose([(2,0,0),(0,0,0)],matches=[[1,2],[2,1]])
    s=selection.PoseSelector(core=validator());s.add(p,1)
    assert s.poses[1].property['r_graft_core_rmsd']==0


def test_nonfinite_core_coordinates_fail_closed():
    s=selection.PoseSelector(core=validator())
    with pytest.raises(selection.PoseGeometryError):s.add(Pose([(float('nan'),0,0),(2,0,0)]),1)
    assert not s.poses


def test_reference_mapping_is_checked_instead_of_rechosen():
    with pytest.raises(selection.PoseGeometryError):
        selection.CoreValidator(Pose([(0,0,0),(2,0,0)]),'CC',[2,1],matcher=matcher)


def test_load_native_frozen_input_and_reference_relative_to_input(tmp_path):
    path=tmp_path/'glide.in'
    path.write_text('CORE_DEFINITION smarts\nCORE_SMARTS "CC"\nCORE_ATOMS 1,2\nCORE_RESTRAIN True\nCORE_POS_MAX_RMSD 1.0\nREF_LIGAND_FILE ref.maegz\n')
    def reader(name):
        assert Path(name)==tmp_path/'ref.maegz'
        return iter([Pose([(0,0,0),(2,0,0)])])
    v=selection.CoreValidator.from_input(path,structure=SimpleNamespace(StructureReader=reader),matcher=matcher)
    assert v.rmsd(Pose([(0,0,0),(2,0,0)]))==0
    path.write_text(path.read_text().replace('CORE_RESTRAIN True','CORE_RESTRAIN False'))
    with pytest.raises(selection.PoseGeometryError):
        selection.CoreValidator.from_input(path,structure=SimpleNamespace(StructureReader=reader),matcher=matcher)


def test_no_input_or_ambiguous_input_never_disables_validation(tmp_path):
    with pytest.raises(selection.PoseGeometryError):selection.CoreValidator.from_directory(tmp_path)
    (tmp_path/'one.in').touch();(tmp_path/'two.in').touch()
    with pytest.raises(selection.PoseGeometryError):selection.CoreValidator.from_directory(tmp_path)


def test_empty_validated_extraction_archives_old_pose_and_records_failure(tmp_path):
    from test_glide_timeout_results import structure_module
    p=Pose([(0,0,0),(2,0,0)],matches=[])
    scores=tmp_path/'docking_scores.tsv';poses=tmp_path/'best_poses.maegz'
    poses.write_text('old rejected pose')
    s=selection.extract('input',scores,poses,structure=structure_module({'input':[p]}),core=validator())
    assert not s.poses and not poses.exists()
    assert len(list(tmp_path.glob('best_poses.rejected-*.maegz')))==1
    assert scores.read_text()=='id\tdocking_score\n'
    report=json.loads((tmp_path/'core_validation.json').read_text())
    assert report['rejected']=={'1':{'no_core_match':1}}


def test_timeout_collection_uses_same_core_filter_and_records_diagnostics(tmp_path, monkeypatch):
    from graft import glide_timeout_results as timeout
    from test_glide_timeout_results import structure_module
    good=Pose([(0,0,0),(2,0,0)],score=-7)
    bad=Pose([(10,0,0),(12,0,0)],score=-50)
    missing=Pose([(0,0,0),(2,0,0)],score=-100,matches=[])
    called=[]
    def load(path):
        called.append(path)
        return validator()
    monkeypatch.setattr(timeout.CoreValidator,'from_input',load)
    report=timeout.collect(dict(inputs=['input'],ids=[1],output=str(tmp_path),core_input='frozen.in'),
                           structure_module({'input':[bad,good,missing]}))
    assert called==['frozen.in'] and report['scored']==1
    assert report['core_validation']['rejected'][1]=={'core_rmsd_exceeded':1,'no_core_match':1}
    assert '-7' in (tmp_path/'docking_scores.tsv').read_text()


def test_score_wrapper_accepts_empty_validated_output_without_a_pose_file(tmp_path, monkeypatch):
    def run(*args,**kwargs):
        (tmp_path/'docking_scores.tsv').write_text('id\tdocking_score\n')
    monkeypatch.setattr(_core,'run_command',run)
    scores,poses=_core.extract_scores_and_best_poses(Path('/suite'),'extractor','input',tmp_path)
    assert scores.exists() and not poses.exists()


def test_score_wrapper_does_not_erase_previous_pose_on_extraction_error(tmp_path, monkeypatch):
    poses=tmp_path/'best_poses.maegz';poses.write_bytes(b'previous')
    def run(*a,**kw):raise RuntimeError('invalid frozen core mapping')
    monkeypatch.setattr(_core,'run_command',run)
    with pytest.raises(RuntimeError,match='invalid frozen core'):
        _core.extract_scores_and_best_poses(Path('/suite'),'extractor','input',tmp_path)
    assert poses.read_bytes()==b'previous'


def test_unverifiable_match_error_is_not_treated_as_a_truncated_input(tmp_path):
    def broken(st,smarts,unique=False):raise RuntimeError('matcher failed')
    v=validator();v.matcher=broken
    with pytest.raises(selection.PoseGeometryError,match='Cannot verify'):
        v.rmsd(Pose([(0,0,0),(2,0,0)]))


@pytest.mark.parametrize('text',['', 'garbage\n', 'id\tdocking_score\n1\t-7\n'])
def test_score_wrapper_requires_exact_empty_header_when_no_pose_exists(tmp_path,monkeypatch,text):
    def run(*a,**kw):(tmp_path/'docking_scores.tsv').write_text(text)
    monkeypatch.setattr(_core,'run_command',run)
    with pytest.raises(RuntimeError,match='Best-pose'):
        _core.extract_scores_and_best_poses(Path('/suite'),'extractor','input',tmp_path)


def test_all_rejected_group_finishes_with_failure_instead_of_repeated_worker_crash(tmp_path,monkeypatch):
    from graft import database
    from graft.stages import glide
    from test_glide_timeout_results import structure_module
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(1,'CMPID_1','CC',1,'glide_running')")
    folder=tmp_path/'ref_1';folder.mkdir();source=folder/'glide_lib.maegz'
    invalid=Pose([(0,0,0),(2,0,0)],matches=[])
    def run(*a,**kw):
        selection.extract(source,folder/'docking_scores.tsv',folder/'best_poses.maegz',
                          structure=structure_module({str(source):[invalid]}),core=validator())
    monkeypatch.setattr(_core,'run_command',run)
    args=SimpleNamespace(db_path=db,schrodinger=Path('/suite'))
    glide.process_completed_job(1,args,dict(reference_id=1,group_dir=folder,output_file=source,
                                compounds=[(1,'CMPID_1','CC',1.)]),'extractor','merger',tmp_path)
    with database.connect(db) as conn:
        row=conn.execute('SELECT state,failed_stage,failure_message,docking_score FROM compound WHERE id=1').fetchone()
    assert row==('failed','glide','No Glide pose passed core validation: no_core_match',None)
    assert not (folder/'best_poses.maegz').exists()


@pytest.mark.parametrize('legacy_header',[False,True])
def test_generated_stage_extractor_enforces_core_even_with_an_old_header(tmp_path,monkeypatch,legacy_header):
    import runpy,sys
    from test_glide_timeout_results import structure_module
    ref=Pose([(0,0,0),(2,0,0)])
    bad=Pose([(10,0,0),(12,0,0)],score=-50)
    good=Pose([(0,0,0),(2,0,0)],score=-7)
    path=tmp_path/'glide.in'
    path.write_text('CORE_DEFINITION smarts\nCORE_SMARTS "CC"\nCORE_ATOMS 1,2\nCORE_RESTRAIN True\nCORE_POS_MAX_RMSD 1.0\nREF_LIGAND_FILE ref.maegz\n')
    structure=structure_module({'input':[bad,good],str(tmp_path/'ref.maegz'):[ref]})
    monkeypatch.setitem(sys.modules,'schrodinger',SimpleNamespace(structure=structure,adapter=SimpleNamespace(evaluate_smarts=matcher)))
    script=_core.create_score_extractor(tmp_path,core_validation=True)
    if legacy_header:script.write_text(script.read_text().replace('REQUIRE_CORE_VALIDATION = True\n',''))
    scores,poses=tmp_path/'scores.tsv',tmp_path/'best_poses.maegz'
    monkeypatch.setattr(sys,'argv',[str(script),'input',str(scores),str(poses)])
    runpy.run_path(str(script),run_name='__main__')
    assert scores.read_text()=='id\tdocking_score\n1\t-7.0\n'
    assert json.loads(poses.read_text())[0]['properties']['r_graft_core_rmsd']==0
