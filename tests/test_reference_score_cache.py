"""Cached native ensembles can seed a fresh DB without pretending to own jobs."""
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database, driver, md_gbsa, reference_scores as cache
from graft.states import CompoundState as State


@pytest.fixture
def args(tmp_path):
    grid=tmp_path/'grid.zip';grid.write_bytes(b'target grid')
    source=tmp_path/'references.maegz';source.write_bytes(b'original reference poses')
    args=SimpleNamespace(output=tmp_path,db_path=tmp_path/'old.sqlite',grid=grid,reference_poses=source,
        schrodinger=Path('/suite'),amber_charge_method='bcc',amber_remote={'remote_env':'/amber'},
        prime_gbsa_enabled=True,prime_gbsa_flexdist=6.,prime_gbsa_local_compute=True,
        prime_gbsa_local_schrodinger='/prime',md_gbsa_production_ns=2.5,md_gbsa_equilibration_ns=1.,
        md_gbsa_snapshots=2,md_gbsa_analysis_start_fraction=.2,md_gbsa_seed=11,
        md_gbsa_replicates=1,md_gbsa_schrodinger='/md-suite',md_gbsa_local_compute=True,
        md_gbsa_local_schrodinger='/md-prime',md_gbsa_input_count=2,md_gbsa_input_ratio=.5,
        attraction_point=None,md_gbsa_enabled=True,amber_gbsa_enabled=True,target_count=2,
        prime_gbsa_input_count=2,amber_keep_count=500)
    fresh(args.db_path,[10,20])
    return args


def fresh(path, ids):
    with database.connect(path) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state,docking_score) VALUES (?,?,?,0,'reference',-10)",
                         [(ids[0],'a','CC'),(ids[1],'b','CCC')])


def completed(args):
    context=cache.context(args)
    settings=dict(protocol=context['md']['protocol'],replicates=1,suite='/md-suite',
                  helper_sha256=context['md']['implementation'],
                  local_compute=dict(suite='/md-prime'))
    with database.connect(args.db_path) as conn:
        database.ensure_md_gbsa_schema(conn)
        conn.execute('INSERT INTO iteration_md_gbsa_selection VALUES (0,?,?)',(json.dumps([20,10]),json.dumps(settings)))
        conn.execute('UPDATE compound SET amber_gbsa_score=-20,prime_gbsa_score=-30')
    for cid in (10,20):
        result=dict(id=cid,mean=-41.,std=math.sqrt(2),protocol=settings['protocol'],
                    snapshots=[dict(frame=0,score=-40.),dict(frame=1,score=-42.)],
                    native=[dict(md=dict(job_id='old-native-job'))],local_compute=dict(root='/old-run'))
        database.store_md_gbsa_result(args.db_path,0,cid,result)
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE md_gbsa_local_cleanup SET completed=1')
        conn.execute('UPDATE md_gbsa_remote_cleanup SET completed=1')
    args.amber_receptor=args.output/'iter00/08-prime_gbsa/receptor.maegz'
    args.amber_receptor.parent.mkdir(parents=True,exist_ok=True)
    args.amber_receptor.write_bytes(b'receptor geometry')
    for stage,record in (
        ('amber_gbsa',dict(compound_ids=[10,20],charge_method='bcc',settings=dict(remote_env='/amber'),
            source_fingerprints={str(args.reference_poses.resolve()):cache.digest(args.reference_poses),
                                 str(args.amber_receptor.resolve()):cache.digest(args.amber_receptor)})),
        ('prime_gbsa',dict(compound_ids=[10,20],command=['/prime/prime_mmgbsa','-rflexdist','6.0']))):
        folder=args.output/'iter00'/({'amber_gbsa':'07-amber_gbsa','prime_gbsa':'08-prime_gbsa'}[stage])
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'run.completed.json').write_text(json.dumps(record))
    return context


@pytest.fixture
def native_file(monkeypatch):
    source=dict(sha256='file-sha',signature='all-poses',records=[
        dict(index=1,name='a',smiles='CC',signature='pose-a',cache=None),
        dict(index=2,name='b',smiles='CCC',signature='pose-b',cache=None)])
    def native(args,action='inspect',request=None,source=None):
        if action=='update':
            assert request['sha256']==native_file['sha256']
            for row in native_file['records']:
                update=request['updates'][row['index']]
                row['cache']=json.dumps(update)
                row['scores']=update['scores']
                row['md_std']=update['md_result']['std'] if update.get('md_result') else None
                row['md_snapshots']=len(update['md_result']['snapshots']) if update.get('md_result') else None
        return copy.deepcopy(native_file)
    native_file=source
    monkeypatch.setattr(cache,'native',native)
    return source


def new_project(args,tmp_path):
    result=copy.copy(args)
    result.output=tmp_path/'new';result.output.mkdir()
    result.db_path=result.output/'new.sqlite'
    result.amber_receptor=result.output/'iter00/08-prime_gbsa/receptor.maegz'
    fresh(result.db_path,[7,11])
    return result


def test_writeback_and_reuse_all_three_scores_with_new_ids_and_no_native_jobs(args,native_file,tmp_path,monkeypatch):
    completed(args)
    assert cache.publish(args)==2
    first=json.loads(native_file['records'][0]['cache'])
    assert first['scores']==dict(amber_gbsa=-20.,prime_gbsa=-30.,md_gbsa=-41.)
    assert first['md_selection']==['b','a']
    new=new_project(args,tmp_path)
    assert cache.restore(new)==2
    with database.connect(new.db_path) as conn:
        assert conn.execute('SELECT id,amber_gbsa_score,prime_gbsa_score,md_gbsa_score,md_gbsa_std,md_gbsa_snapshots,state FROM compound ORDER BY id').fetchall()==[
            (cid,-20.,-30.,-41.,math.sqrt(2),2,State.MD_GBSA_DONE) for cid in (7,11)]
        for encoded, in conn.execute('SELECT result FROM compound_md_gbsa_result'):
            result=json.loads(encoded)
            assert result['id'] in (7,11) and 'native' not in result and 'local_compute' not in result
    assert database.md_gbsa_selection(new.db_path,0)['ids']==[11,7]
    assert not driver.reference_work_pending(new)
    monkeypatch.setattr(md_gbsa,'ssh',lambda *a,**kw:pytest.fail('Cache attempted a native operation'))
    monkeypatch.setattr(md_gbsa,'prepare',lambda *a:pytest.fail('Cache attempted input preparation'))
    md_gbsa.run(new,0)
    assert cache.publish(new)==0


def test_changed_grid_or_pose_never_reuses_cache(args,native_file,tmp_path):
    completed(args);cache.publish(args)
    new=new_project(args,tmp_path)
    new.grid=tmp_path/'different-grid.zip';new.grid.write_bytes(b'different target')
    assert cache.restore(new)==0
    new.grid=args.grid
    native_file['signature']='changed geometry'
    assert cache.restore(new)==0
    assert database.md_gbsa_selection(new.db_path,0) is None


@pytest.mark.parametrize('change',['protocol','missing','duplicate','statistics','nonfinite'])
def test_incompatible_or_incomplete_md_never_establishes_completion(args,native_file,tmp_path,change):
    completed(args);cache.publish(args)
    new=new_project(args,tmp_path)
    if change=='protocol':
        new.md_gbsa_production_ns=5.
    else:
        value=json.loads(native_file['records'][0]['cache'])
        result=value['md_result']
        if change=='missing':result['snapshots'].pop()
        elif change=='duplicate':result['snapshots'][1]['frame']=0
        elif change=='statistics':result['std']=123.
        elif change=='nonfinite':result['snapshots'][0]['score']=float('nan')
        native_file['records'][0]['cache']=json.dumps(value)
    cache.restore(new)
    with database.connect(new.db_path) as conn:
        assert conn.execute('SELECT amber_gbsa_score,prime_gbsa_score,md_gbsa_score FROM compound').fetchall()==[(-20.,None,None)]*2
    assert database.md_gbsa_selection(new.db_path,0) is None
    assert driver.reference_work_pending(new)


def test_existing_scientific_work_is_never_overwritten(args,native_file):
    completed(args);cache.publish(args)
    before=database.reference_score_records(args.db_path)
    with pytest.raises(RuntimeError,match='fresh'):
        cache.restore(args)
    assert database.reference_score_records(args.db_path)==before


def test_cache_import_validation_is_atomic(args):
    updates={'a':dict(smiles='CC',scores={'amber_gbsa_score':-20}),
             'b':dict(smiles='WRONG',scores={'amber_gbsa_score':-21})}
    with pytest.raises(ValueError,match='identity'):
        database.restore_reference_score_records(args.db_path,updates,None)
    assert all(r['amber_gbsa_score'] is None for r in database.reference_score_records(args.db_path))


def test_pending_md_never_writes_input_file(args,native_file,monkeypatch):
    completed(args)
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET md_gbsa_score=NULL,state=? WHERE id=10',(State.MD_GBSA_RUNNING,))
    monkeypatch.setattr(cache,'native',lambda *a,**kw:pytest.fail('Pending work wrote cache'))
    assert cache.publish(args)==0


def test_identity_mismatch_never_updates_reference(args,native_file):
    completed(args)
    native_file['records'][0]['smiles']='CO'
    with pytest.raises(ValueError,match='identity'):
        cache.publish(args)
    assert all(r['cache'] is None for r in native_file['records'])


def test_md_worker_publishes_reference_scores_only_after_reference_run(args,monkeypatch):
    from graft.stages import md_gbsa as stage
    calls=[]
    monkeypatch.setattr(stage.md_gbsa,'run',lambda *a:calls.append('run'))
    monkeypatch.setattr(cache,'publish',lambda *a:calls.append('publish'))
    stage._run_stage(args,0)
    assert calls==['run','publish']
    calls.clear();stage._run_stage(args,1)
    assert calls==['run']


def test_only_proven_metadata_annotations_can_reuse_frozen_parent_exports(args,monkeypatch):
    saved=dict(sources={'parents':dict(path=str(args.reference_poses),sha256='old'),
                        'grid':dict(path='grid.zip',sha256='target')},suite='/suite',helper_sha256='helper')
    current=copy.deepcopy(saved);current['sources']['parents']['sha256']='annotated'
    monkeypatch.setattr(cache,'annotation_matches',lambda *a:True)
    assert cache.preserve_parent_signature(current,saved)==saved
    changed=copy.deepcopy(current);changed['sources']['grid']['sha256']='other-target'
    with pytest.raises(RuntimeError,match='sources changed'):
        cache.preserve_parent_signature(changed,saved)
    monkeypatch.setattr(cache,'annotation_matches',lambda *a:False)
    with pytest.raises(RuntimeError,match='sources changed'):
        cache.preserve_parent_signature(current,saved)


def test_unknown_upstream_settings_are_displayed_but_never_certified_for_reuse(args,native_file,tmp_path):
    completed(args)
    args.amber_charge_method='abcg2'  # Changed after the original bcc run.
    assert cache.publish(args)==2
    assert json.loads(native_file['records'][0]['cache'])['context']['amber'] is None
    new=new_project(args,tmp_path)
    assert cache.restore(new)==0
    assert database.md_gbsa_selection(new.db_path,0) is None


def test_native_maegz_metadata_roundtrip_and_backup(args,tmp_path):
    import subprocess
    from rdkit import Chem
    suite=Path('/share/opt/schrodinger/2026-1')
    if not (suite/'run').is_file():
        pytest.skip('Installed Schrödinger structure I/O unavailable')
    sdf=tmp_path/'input.sdf'
    with Chem.SDWriter(str(sdf)) as writer:
        for name,smiles in [('a','CC'),('b','CCC')]:
            mol=Chem.AddHs(Chem.MolFromSmiles(smiles))
            conf=Chem.Conformer(mol.GetNumAtoms());conf.Set3D(True)
            for i in range(mol.GetNumAtoms()):conf.SetAtomPosition(i,(i*.7,i*.3,i*.2))
            mol.AddConformer(conf);mol.SetProp('_Name',name);mol.SetProp('s_user_note','original note')
            writer.write(mol)
    source=tmp_path/'references.maegz'
    script=tmp_path/'graft_make_reference.py'
    script.write_text('''from schrodinger import structure
import sys
with structure.StructureWriter(sys.argv[2]) as writer:
 for st in structure.StructureReader(sys.argv[1]): writer.append(st)
''')
    subprocess.run([str(suite/'run'),str(script),str(sdf),str(source)],check=True,capture_output=True)
    args.schrodinger=suite
    args.reference_poses=source
    before=cache.native(args)
    request=dict(sha256=before['sha256'],updates={r['index']:dict(
        pose_signature=r['signature'],source_signature=before['signature'],
        scores=dict(amber_gbsa=-20.,prime_gbsa=-30.,md_gbsa=-40.),md_result=None)
        for r in before['records']})
    after=cache.native(args,'update',request)
    assert after['signature']==before['signature']
    assert [r['name'] for r in after['records']]==['a','b']
    assert cache.annotation_matches(args,before['sha256'])
    assert all(json.loads(r['cache'])['scores']['md_gbsa']==-40. for r in after['records'])
    assert len(list(tmp_path.glob('*.graft-backup-*.maegz')))==1
    request['sha256']=after['sha256']
    assert cache.native(args,'update',request)['sha256']==after['sha256']
    # Real MAEGZ -> structconvert -> new SQLite import -> complete ensemble reuse.
    # Energies are synthetic; this validates I/O/reuse, not native MD accuracy.
    completed(args)
    assert cache.publish(args)==2
    new=copy.copy(args)
    new.output=tmp_path/'fresh';new.output.mkdir()
    new.db_path=new.output/'project.sqlite'
    new.amber_receptor=new.output/'iter00/08-prime_gbsa/receptor.maegz'
    assert _core.initialize_project_database(new.db_path,new.reference_poses,suite)
    assert cache.restore(new)==2
    assert not driver.reference_work_pending(new)
    with database.connect(new.db_path) as conn:
        assert conn.execute('SELECT name,md_gbsa_score FROM compound ORDER BY id').fetchall()==[('a',-41.),('b',-41.)]


def test_interrupted_cache_import_retries_initialization_and_commits_marker_atomically(args,native_file,tmp_path,monkeypatch):
    completed(args);cache.publish(args)
    new=new_project(args,tmp_path)
    with database.connect(new.db_path) as conn:
        database.mark_reference_cache_import(conn)
    with monkeypatch.context() as patch:
        patch.setattr(cache,'native',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('interrupted cache I/O')))
        with pytest.raises(RuntimeError,match='interrupted'):
            cache.restore(new)
    assert database.reference_cache_import_pending(new.db_path)
    assert _core.initialize_project_database(new.db_path,new.reference_poses,new.schrodinger)
    assert cache.restore(new)==2
    assert not database.reference_cache_import_pending(new.db_path)


def test_uncached_or_invalid_input_finishes_pending_marker_without_claiming_work(args,native_file):
    with database.connect(args.db_path) as conn:
        database.mark_reference_cache_import(conn)
    assert cache.restore(args)==0
    assert not database.reference_cache_import_pending(args.db_path)
    assert all(r['md_gbsa_score'] is None for r in database.reference_score_records(args.db_path))


def test_generated_amber_rebuilds_default_receptor_after_reference_cache_skip(args,monkeypatch):
    from graft import amber_gbsa
    from graft.stages import amber_gbsa as stage
    args.target_count=1
    args.amber_remote.update(host='slurm',remote_work_dir='/runs')
    args.amber_receptor=args.output/'iter00/08-prime_gbsa/receptor.maegz'
    with database.connect(args.db_path) as conn:
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,docking_score) VALUES (30,'CMPID_30','CO',1,'docked',-9)")
    calls=[]
    def extract(command,**kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b'grid receptor')
    monkeypatch.setattr(_core,'run_command',extract)
    monkeypatch.setattr(amber_gbsa,'new_record',lambda a,i,d,ids:dict(compound_ids=ids))
    def poll(a,i,d,record):
        database.store_amber_results(a.db_path,i,[dict(id=30,score=-21)],record['compound_ids'])
        return True
    monkeypatch.setattr(amber_gbsa,'poll',poll)
    stage._run_stage(args,1)
    assert args.amber_receptor.is_file()
    assert len(calls)==1 and calls[0][-2]==args.grid


def test_new_prime_queue_pins_installation_for_cache_provenance(args):
    from graft import prime_gbsa_queue
    folder=args.output/'queue-test';folder.mkdir()
    record=prime_gbsa_queue.create_queue(args,folder,[10,20])
    assert record['schrodinger']=='/suite'
    args.schrodinger=Path('/different-suite')
    with pytest.raises(RuntimeError,match='installation changed'):
        prime_gbsa_queue.poll_queue(0,args,folder,record)


def test_terminal_failed_md_cannot_erase_a_successful_reference_file_cache(args,native_file,monkeypatch):
    completed(args);cache.publish(args)
    before=copy.deepcopy(native_file)
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET md_gbsa_score=NULL,state='failed',failed_stage='md_gbsa'")
    assert database.md_gbsa_completion_reason(args.db_path,0) is None
    monkeypatch.setattr(cache,'native',lambda *a,**kw:pytest.fail('Failed ensembles overwrote reference cache'))
    assert cache.publish(args)==0
    assert native_file==before
