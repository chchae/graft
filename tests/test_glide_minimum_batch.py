"""Minimum original-molecule packing never mixes references or splits states."""
from collections import Counter

import pytest

from graft import database
from graft import glide_license_inputs as native
from graft.stages import glide_shared as shared
from test_glide_license_retry import project, structures, Pose, write_poses


@pytest.mark.parametrize(('minimum','count','sizes'), [
    (10,0,[]), (10,7,[7]), (10,9,[9]), (10,10,[10]), (10,11,[11]),
    (10,19,[19]), (10,20,[10,10]), (10,21,[10,11]), (10,29,[10,19]),
    (10,30,[10,10,10]), (10,31,[10,10,11]), (10,41,[10,10,10,11]),
    (20,0,[]), (20,7,[7]), (20,19,[19]), (20,20,[20]), (20,21,[21]), (20,39,[39]),
    (20,40,[20,20]), (20,41,[20,21]), (20,59,[20,39]), (20,60,[20,20,20]),
    (20,61,[20,20,21]), (20,86,[20,20,20,26]),
])
def test_minimum_overrides_small_state_target_and_merges_tail(project, minimum, count, sizes):
    _, _, group = project
    ligands = group/'ligprep_group.maegz'
    ids = list(range(1,count+1))
    # Keep nonadjacent states of each original molecule in the same task.
    poses = [Pose(cid) for _ in range(2) for cid in ids]
    write_poses(ligands,poses)
    request = dict(input=str(group/'glide_10.in'),ligands=str(ligands),
                   ids=ids+[999],batch_size=3,min_compounds=minimum)
    result = native.ordinary_plan(request,structures())
    batches = list(result['tasks'].values())
    assert [len(t['ids']) for t in batches] == sizes
    assert [cid for t in batches for cid in t['ids']] == ids
    assert sorted(i for t in batches for i in t['indices']) == list(range(1,len(poses)+1))
    for task in batches:
        selected = Counter(int(poses[i-1].title.removeprefix('CMPID_')) for i in task['indices'])
        assert selected == Counter({cid:2 for cid in task['ids']})
    assert result['missing'] == [999]
    again = native.ordinary_plan(request,structures())
    assert again == result


def test_larger_adaptive_target_can_pack_more_than_minimum(project):
    _, _, group = project
    ids = list(range(1,121))
    write_poses(group/'ligprep_group.maegz',[Pose(cid) for cid in ids for _ in range(2)])
    result = native.ordinary_plan(dict(input=str(group/'glide_10.in'),ligands=str(group/'ligprep_group.maegz'),
        ids=ids,batch_size=100,min_compounds=10),structures())
    assert [len(t['ids']) for t in result['tasks'].values()] == [50,50,20]


@pytest.mark.parametrize('minimum', [10,20])
def test_whole_state_set_can_exceed_state_budget(project,minimum):
    _, _, group = project
    ids = list(range(1,41))
    poses = [Pose(cid) for cid in ids for _ in range(10 if cid==minimum else 2)]
    write_poses(group/'ligprep_group.maegz',poses)
    result = native.ordinary_plan(dict(input=str(group/'glide_10.in'),ligands=str(group/'ligprep_group.maegz'),
        ids=ids,batch_size=3,min_compounds=minimum),structures())
    batches = list(result['tasks'].values())
    assert [len(t['ids']) for t in batches] == [minimum]*(40//minimum)
    assert len(batches[0]['indices']) == minimum*2+8
    assert minimum not in batches[1]['ids']


@pytest.mark.parametrize('minimum', [0,-1,True,2.5])
def test_invalid_minimum_rejected_before_planning(project,minimum):
    _, _, group = project
    with pytest.raises(ValueError,match='positive integer'):
        native.ordinary_plan(dict(input=str(group/'glide_10.in'),ligands=str(group/'ligprep_group.maegz'),
            ids=[1,2,3],batch_size=3,min_compounds=minimum),structures())


def test_enrollment_passes_minimum_and_keeps_frozen_plan(project,monkeypatch,capsys):
    args, root, group = project
    (group/'glide_job.json').unlink()
    other = root/'ref_20'
    other.mkdir()
    (other/'grid.zip').write_bytes(b'second grid')
    (other/'_reference.maegz').write_bytes(b'second reference')
    (other/'glide_20.in').write_text((group/'glide_10.in').read_text().replace(str(group),str(other)))
    write_poses(other/'ligprep_group.maegz',[Pose(4),Pose(4),Pose(5)])
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET state='glide_running',docking_score=NULL,failed_stage=NULL,failure_message=NULL WHERE iteration=1")
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(20,'REF20','C',0,'reference')")
        for cid in (4,5):
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) VALUES(?,?,?,1,'glide_running',20)",
                         (cid,f'CMPID_{cid}','CC'))
    monkeypatch.setattr(shared.database,'glide_observed_batch_size',lambda *a:3)
    requests = []
    original = shared.retry._helper
    def helper(args,folder,request):
        requests.append(request)
        return original(args,folder,request)
    monkeypatch.setattr(shared.retry,'_helper',helper)
    entries = [(dict(group_dir=group,reference_id=10,compounds=[(1,),(2,),(3,)]),group/'ligprep_group.maegz'),
               (dict(group_dir=other,reference_id=20,compounds=[(4,),(5,)]),other/'ligprep_group.maegz')]
    inputs = {10:group/'glide_10.in',20:other/'glide_20.in'}
    shared.enroll(args,1,entries,inputs,75)
    assert [r['min_compounds'] for r in requests] == [10,10]
    assert 'minimum 10 compounds/batch' in capsys.readouterr().out
    frozen = database.glide_license_retries(args.db_path,1)
    assert [(t['reference_id'],t['payload']['ids']) for t in frozen] == [(10,[1,2,3]),(20,[4,5])]
    monkeypatch.setattr(shared,'GLIDE_SHARED_MIN_COMPOUNDS',20)
    with pytest.raises(RuntimeError,match='plan changed'):
        shared.enroll(args,1,entries,inputs,75)
    assert database.glide_license_retries(args.db_path,1) == frozen


def test_minimum_is_part_of_source_fingerprint_even_for_small_references(project):
    _, _, group = project
    request = dict(input=str(group/'glide_10.in'),ligands=str(group/'ligprep_group.maegz'),
                   ids=[1,2,3],batch_size=100)
    small = native.ordinary_plan(dict(request,min_compounds=10),structures())
    other = native.ordinary_plan(dict(request,min_compounds=20),structures())
    assert small['tasks'] == other['tasks']
    assert small['source'] != other['source']
