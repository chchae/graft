"""Fixed-frame CReM chemistry, durable selection and synthetic CPU integration.

The tiny generated CReM DB and distant receptor do not validate target affinity.
"""
import json
import csv
import sqlite3
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from graft import database
from graft.config import combichem_3d_settings
from graft.chemistry.combichem3d import crem_growth, frames, history as provenance
from graft.chemistry.combichem3d.assembly import assemble
from graft.chemistry.combichem3d.attachment_policy import growth_available, root_for_site
from graft.chemistry.combichem3d.output import export_results
from graft.stages import combichem_3d as stage


@pytest.fixture
def project(tmp_path):
    pytest.importorskip('crem')
    from crem.db import create_db
    library=tmp_path/'fragments.db'
    create_db(['CCc1ccccc1','CCCc1ccccc1','Cc1ccc(C)cc1','CCc1ccc(CC)cc1',
               'CCCCC','CCCCCC','CCN','CCO'],library,'test',radii=(1,),ncpu=1,
              max_heavy_atoms=3,frag_mode='acyclic',verbose=False)
    mol=Chem.AddHs(Chem.MolFromSmiles('CCc1ccc(CC)cc1'))
    assert AllChem.EmbedMolecule(mol,randomSeed=37)==0
    AllChem.MMFFOptimizeMolecule(mol)
    with Chem.SDWriter(str(tmp_path/'references.sdf')) as writer:
        writer.write(mol)
    receptor=Chem.MolFromSmiles('[Ne]')
    conf=Chem.Conformer(1);conf.Set3D(True);conf.SetAtomPosition(0,(100,100,100));receptor.AddConformer(conf)
    with Chem.SDWriter(str(tmp_path/'receptor.sdf')) as writer:
        writer.write(receptor)
    path=tmp_path/'egfr.toml'
    path.write_text('''out-dir = "output"
reference-pose = "references.sdf"
reference-mcs = "c1([*:1])ccc([*:3])cc1"
receptor = "receptor.sdf"
max-iteration = 100
sidechain-max-heavy-atoms = 8
[combichem-3d]
generation-backend = "crem"
crem-db = "fragments.db"
crem-radius = 1
max-iteration = 2
sidechains-per-site = 2
sidechain-min-heavy-atoms = 1
sidechain-max-heavy-atoms = 2
elite-count = 2
processes = 2
batch-size = 1
max-seconds = 60
''')
    return combichem_3d_settings(path)


def test_existing_project_inputs_and_crem_defaults(project):
    cfg=project
    assert cfg['growth-mode']=='fixed-frame'
    assert cfg['stop-mode']=='cycles'
    assert cfg['elite-rank-by']=='qed'
    assert cfg['conformers']==2 and cfg['optimization-iterations']==100
    assert cfg['sidechain-library']==cfg['crem-db']
    assert cfg['out-dir'].endswith('output/combichem-3d')
    path=cfg['config-path']
    text=open(path).read().replace('elite-count = 2\n','').replace('sidechain-max-heavy-atoms = 2\n','')
    with open(path,'w') as stream:stream.write(text)
    defaults=combichem_3d_settings(path)
    assert defaults['elite-count']==100 and defaults['sidechain-max-heavy-atoms']==10


@pytest.mark.parametrize('setting,message',[
    ('growth-mode = "frame-cycles"','fixed-frame'),
    ('core-constraint = "restraint"','core-constraint'),
    ('initial-attachment-max-cycles = {"1"=2}','budgets'),
    ('crem-min-freq = -1','crem-min-freq'),
    ('crem-radius = true','crem-radius'),
    ('execution-backend = "slurm"','local multiprocessing'),
    ('sidechain-include-h = true','one non-H'),
])
def test_incompatible_protocols_rejected(project,setting,message):
    path=project['config-path']
    text=open(path).read()
    key=setting.split(' = ')[0]
    text='\n'.join(line for line in text.splitlines() if not line.startswith(key+' = '))
    with open(path,'w') as stream:stream.write(text+'\n'+setting+'\n')
    with pytest.raises(ValueError,match=message):combichem_3d_settings(path)


def test_frame_budgets_protection_and_all_branch_atoms(project):
    frame,h=frames.initial_frame(project['parents'],project['reference-mcs'],growth_mode='fixed-frame')
    assert frame.GetNumHeavyAtoms()==6 # original reference decorations are removed
    assert h['initial_attachment_max_cycles']=={'0':1,'1':3}
    assert set(crem_growth.candidate_sites(frame,h))==set(h['attachments'])
    product,birth=assemble(frame,h,[h['attachments'][0]],['*CC'],'first')
    assert birth['growth_counts']=={'0':1,'1':0}
    assert not growth_available(birth,'0')
    capped_site=next(a['uid'] for a in birth['atoms'] if a['branch']=='branch_1')
    with pytest.raises(ValueError,match='cycle limit'):
        assemble(product,birth,[capped_site],['*C'],'forbidden')
    # Grow the other branch twice; old as well as newest fragment atoms remain eligible.
    product,birth=assemble(product,birth,[h['attachments'][1]],['*CC'],'second')
    old_sites=crem_growth.candidate_sites(product,birth)
    branch_site=next(uid for uid in old_sites if uid not in h['attachments'])
    product,birth=assemble(product,birth,[branch_site],['*C'],'third')
    sites=crem_growth.candidate_sites(product,birth)
    assert branch_site in sites
    assert all(root_for_site(birth,uid)=='1' for uid in sites)
    assert birth['growth_counts']=={'0':1,'1':2}
    product,birth=assemble(product,birth,[branch_site],['*C'],'fourth')
    assert not crem_growth.candidate_sites(product,birth)


def test_unmapped_dummy_is_unlimited_and_999_is_literal(project):
    frame,h=frames.initial_frame(project['parents'],'c1([*])ccc([*:999])cc1',growth_mode='fixed-frame')
    assert h['initial_attachment_max_cycles']=={'0':None,'1':999}
    h['growth_counts']={'0':5000,'1':999}
    assert growth_available(h,'0') and not growth_available(h,'1')
    with pytest.raises(ValueError,match='positive growth counts'):
        frames.initial_frame(project['parents'],'c1([*:0])ccc([*:3])cc1',growth_mode='fixed-frame')


def test_real_crem_candidates_reassemble_at_only_requested_anchor(project):
    db,_=stage.prepare_project(project)
    row=database.growth_parent(db,1);frame=stage.restore_parent(row)
    candidates=crem_growth.site_candidates(frame,row['history'],row['history']['attachments'][0],project)
    assert candidates and len(candidates)<=2
    assert candidates==crem_growth.site_candidates(frame,row['history'],row['history']['attachments'][0],project)
    for operation in candidates:
        mol,h=assemble(frame,row['history'],[operation['site']],[operation['fragment']],'actual')
        assert provenance.canonical_smiles(mol)==operation['crem_smiles']
        assert h['growth_counts']=={'0':1,'1':0}


def test_crem_receives_explicit_protection_and_no_nested_pool(project,monkeypatch):
    from crem import crem
    db,_=stage.prepare_project(project)
    original=crem.grow_mol
    observed=[]
    def grow(molecule,*args,**kwargs):
        observed.append((molecule,kwargs))
        return original(molecule,*args,**kwargs)
    monkeypatch.setattr(crem,'grow_mol',grow)
    row=database.growth_parent(db,1);parent=stage.restore_parent(row)
    uid=row['history']['attachments'][0]
    assert crem_growth.site_candidates(parent,row['history'],uid,project)
    molecule,options=observed[0]
    allowed=options['replace_ids']
    assert len(allowed)==1 and molecule.GetAtomWithIdx(allowed[0]).GetProp('_mn_uid')==uid
    assert set(options['protected_ids'])==set(range(molecule.GetNumHeavyAtoms()))-set(allowed)
    assert options['ncores']==1 and options['symmetry_fixes'] is True


def test_unresolved_probe_is_preserved_and_never_generates(project,monkeypatch):
    db,_=stage.prepare_project(project);database.growth_start_session(db,project)
    monkeypatch.setattr(crem_growth,'methyl_probe',lambda *args:dict(status='UNRESOLVED',reason='synthetic embedding failure',pose=None))
    monkeypatch.setattr(crem_growth,'site_candidates',lambda *args:pytest.fail('unresolved site admitted'))
    crem_growth.plan_parent(db,dict(parent_pose=1),project,None,time.time()+30)
    row=database.growth_parent(db,1)
    assert row['plan']['total']==0
    assert len(row['probes'])==2 and all(r['status']=='UNRESOLVED' for r in row['probes'].values())
    database.growth_end_session(db,'exhausted')
    assert export_results(db,project['out-dir'])['partial_search']


def test_probe_rejection_blocks_crem_and_saved_pass_pools_resume(project,monkeypatch):
    db,_=stage.prepare_project(project);database.growth_start_session(db,project)
    row=database.growth_parent(db,1);sites=row['history']['attachments']
    calls=[]
    def probe(parent,h,uid,*args):
        return dict(status='PASS' if uid==sites[0] else 'CLASH_REJECT',reason='synthetic',pose=None)
    def candidates(parent,h,uid,cfg):
        calls.append(uid)
        return [dict(site=uid,fragment='*C',crem_smiles='Cc1ccccc1')]
    monkeypatch.setattr(crem_growth,'methyl_probe',probe)
    monkeypatch.setattr(crem_growth,'site_candidates',candidates)
    job=dict(parent_pose=1)
    crem_growth.plan_parent(db,job,project,None,time.time()+30)
    plan=database.growth_parent(db,1)['plan']
    assert plan['total']==1 and calls==[sites[0]]
    monkeypatch.setattr(crem_growth,'methyl_probe',lambda *a:pytest.fail('saved probe rerun'))
    crem_growth.plan_parent(db,job,project,None,time.time()+30)
    assert calls==[sites[0]] and database.growth_parent(db,1)['plan']==plan


def test_cpu_workers_real_crem_fixed_core_single_additions_and_resume(project):
    cfg=project
    db,first=stage.prepare_project(cfg)
    stage.supervise(cfg,db,first)
    info=export_results(db,cfg['out-dir'])
    assert info['attempted']>0 and info['unique_count']>0
    assert info['stop_reason']=='exhausted' and info['protocol']=='combichem3d_crem_fixed_frame_v1'
    poses=database.growth_rows(db,'combichem3d_pose')
    parents={row['id']:json.loads(row['history']) for row in poses}
    for row in poses:
        h=json.loads(row['history'])
        if not row['generated']:
            assert not row['output_passed']
            continue
        before=parents[row['parent_pose']]
        assert sum(h['growth_counts'].values())==sum(before['growth_counts'].values())+1
        assert h['core']==before['core']
        mol=stage.restore_parent(dict(molblock=row['molblock'],history=h))
        from graft.chemistry.combichem3d.search import core_deviation
        assert core_deviation(mol,h)<1e-6
    selection=database.growth_cycle_info(db,2)
    assert len(selection['selected_pose_ids'])<=2
    expected=database.growth_elite_rows(db,1,2,'qed')
    assert selection['selected_pose_ids']==[r['id'] for r in expected]
    attempted=info['attempted']
    db,first=stage.prepare_project(cfg)
    stage.supervise(cfg,db,first)
    assert database.growth_run_info(db)['attempted']==attempted
    assert database.growth_cycle_info(db,2)==selection


def test_parent_selection_claims_are_atomic_and_wait_for_prior_work(project):
    db,_=stage.prepare_project(project);database.growth_start_session(db,project)
    # Selection must not retire the live previous-cycle parent.
    assert database.growth_claim(db,2,1,99)==dict(kind='wait')
    assert database.growth_cycle_info(db,2)=={}
    row=database.growth_parent(db,1);frame=stage.restore_parent(row)
    database.growth_save_input(db,1,{},False,{})
    database.growth_save_plan(db,1,dict(total=1,sites=[],pools=[]),[])
    batch=database.growth_claim(db,1,1,1)
    for i,qed in enumerate([.1,.9,.7]):
        mol,h=assemble(frame,row['history'],[row['history']['attachments'][0]],['*'+'C'*(i+1)],str(i))
        AllChem.EmbedMolecule(mol,randomSeed=42)
        payload=dict(smiles=provenance.canonical_smiles(mol),molblock=Chem.MolToMolBlock(mol),
                     history=provenance.snapshot(mol,h),properties=dict(qed=qed),output_passed=True)
        with database.growth_connection(db) as conn:
            database._growth_insert_pose(conn,payload,1)
    database.growth_save_attempt(db,batch,0,dict(status='PASS',reason='synthetic',iteration=1))
    database.growth_finish_batch(db,batch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims=list(pool.map(lambda owner:database.growth_claim(db,2,1,owner),range(4)))
    plans=[claim['parent_pose'] for claim in claims if claim and claim['kind']=='plan']
    assert len(plans)==len(set(plans))==2
    selected=database.growth_cycle_info(db,2)['selected_pose_ids']
    assert selected==[r['id'] for r in database.growth_elite_rows(db,1,2,'qed')]
    assert set(plans)==set(selected)


def test_changed_database_blocks_resume_without_discarding_results(project):
    db,_=stage.prepare_project(project)
    with sqlite3.connect(project['crem-db']) as conn:conn.execute('CREATE TABLE changed (id INTEGER)')
    with pytest.raises(RuntimeError,match='input_sha256'):
        stage.prepare_project(project)
    assert len(database.growth_rows(db,'compound'))==1


def test_cli_restarts_existing_output_after_frame_budget_change(project):
    path=Path(project['config-path'])
    stage._main([str(path)])
    output=Path(project['out-dir'])
    marker=output/'input-cache'/'old-result.txt'
    marker.write_text('old result')
    sibling=output.parent/'keep-other-project.txt'
    sibling.write_text('keep')
    lock=database.lock_path(output/'combichem3d.sqlite','combichem3d.driver')
    inode=lock.stat().st_ino
    path.write_text(path.read_text().replace('[*:3]','[*:4]'))
    info=stage._main([str(path)])
    assert info['unique_count']>0
    assert not marker.exists() and sibling.read_text()=='keep'
    assert lock.stat().st_ino==inode # a running old-version worker cannot bypass this lock
    seed=json.loads((output/'input-cache/frame-seed/history.json').read_text())
    assert seed['parents'][0]['initial_attachment_max_cycles']=={'0':1,'1':4}


def test_export_only_keeps_existing_output(project,monkeypatch):
    db,_=stage.prepare_project(project)
    marker=Path(project['out-dir'])/'keep.txt'
    marker.write_text('saved result')
    monkeypatch.setattr(stage,'reset_crem_output',lambda cfg:pytest.fail('export deleted output'))
    stage._main([project['config-path'],'--export-only'])
    assert marker.read_text()=='saved result'
    assert len(database.growth_rows(db,'compound'))==1


def test_running_lock_prevents_cli_reset(project):
    db,_=stage.prepare_project(project)
    marker=Path(project['out-dir'])/'keep.txt';marker.write_text('live result')
    with database.stage_lock(db,'combichem3d'):
        with pytest.raises(RuntimeError,match='already running'):
            stage._main([project['config-path']])
    assert marker.read_text()=='live result'


def test_reset_rejects_output_containing_inputs(project):
    path=Path(project['config-path'])
    before=path.read_bytes()
    cfg=project|{'out-dir':str(path.parent)}
    db=path.parent/'combichem3d.sqlite'
    with database.stage_lock(db,'combichem3d'):
        with pytest.raises(ValueError,match='containing input config-path'):
            stage.reset_crem_output(cfg)
    assert path.read_bytes()==before


def test_reset_unlinks_child_symlinks_without_deleting_targets(project,tmp_path):
    db,_=stage.prepare_project(project)
    target=tmp_path/'unrelated';target.mkdir();(target/'keep').write_text('keep')
    alias=Path(project['out-dir'])/'alias';alias.symlink_to(target,target_is_directory=True)
    with database.stage_lock(db,'combichem3d'):
        stage.reset_crem_output(combichem_3d_settings(project['config-path']))
    assert not alias.exists() and (target/'keep').read_text()=='keep'


@pytest.mark.parametrize('remote',[{'execution-backend':'slurm'},{'worker-hosts':{'remote':1}}])
def test_reset_preserves_remote_job_evidence(project,remote):
    db,_=stage.prepare_project(project)
    marker=Path(project['out-dir'])/'keep.txt';marker.write_text('remote evidence')
    saved=database.growth_saved_settings(db)|remote
    with database.growth_connection(db) as conn:
        conn.execute('UPDATE combichem3d_run SET settings=? WHERE id=1',(json.dumps(saved),))
    with database.stage_lock(db,'combichem3d'):
        with pytest.raises(RuntimeError,match='remote calculation pools'):
            stage.reset_crem_output(combichem_3d_settings(project['config-path']))
    assert marker.read_text()=='remote evidence' and Path(db).exists()


def test_cycle_names_smiles_and_pose_exports_keep_internal_identity(project):
    from graft.chemistry.combichem3d.output import log_iteration_results
    db,_=stage.prepare_project(project)
    seed=database.growth_parent(db,1);frame=stage.restore_parent(seed)
    anchor=seed['history']['attachments'][1]
    def payload(mol,h,passed=True):
        mol=Chem.AddHs(mol)
        assert AllChem.EmbedMolecule(mol,randomSeed=37)==0
        return dict(smiles=provenance.canonical_smiles(mol),molblock=Chem.MolToMolBlock(mol),
                    history=provenance.snapshot(mol,h),properties={'qed':.5},output_passed=passed)
    methyl,mh=assemble(frame,seed['history'],[anchor],['*C'],'methyl')
    first=payload(methyl,mh)
    ethyl,eh=assemble(frame,seed['history'],[anchor],['*CC'],'ethyl')
    second=payload(ethyl,eh)
    propyl,ph=assemble(frame,seed['history'],[anchor],['*CCC'],'filtered')
    rejected=payload(propyl,ph,False)
    uid=next(a['uid'] for a in mh['atoms'] if a['branch']=='branch_2')
    repeated,rh=assemble(methyl,mh,[uid],['*C'],'cycle2-ethyl')
    later=payload(repeated,rh)
    assert later['smiles']==second['smiles']
    with database.growth_connection(db) as conn:
        parent=database._growth_insert_pose(conn,first,1)
        database._growth_insert_pose(conn,first,1) # another pose of the same cycle/compound
        database._growth_insert_pose(conn,second,1)
        database._growth_insert_pose(conn,rejected,1)
        database._growth_insert_pose(conn,later,parent)
    before=database.growth_rows(db,'compound')
    history_before=database.growth_rows(db,'combichem3d_pose')
    root=Path(project['out-dir'])
    export_results(db,root,batch=1)
    smi=(root/'compounds.smi').read_text()
    lines=[line.split() for line in smi.splitlines()]
    assert [line[1] for line in lines]==['01_0001','01_0002','02_0001']
    assert lines[1][0]==lines[2][0] and len(lines)==3
    assert rejected['smiles'] not in {line[0] for line in lines}
    with (root/'compounds.csv').open() as stream:records=list(csv.DictReader(stream))
    assert [r['name'] for r in records]==['01_0001','01_0002']
    assert all(r['cmpid']==f"CMPID_{r['id']}" for r in records)
    poses=list(Chem.SDMolSupplier(str(root/'passed_poses.sdf'),removeHs=False))
    assert [m.GetProp('_Name') for m in poses]==['01_0001','01_0001','01_0002','02_0001']
    assert all(m.GetProp('cmpid').startswith('CMPID_') for m in poses)
    assert database.growth_rows(db,'compound')==before
    assert database.growth_rows(db,'combichem3d_pose')==history_before
    export_results(db,root,batch=2)
    assert (root/'compounds.smi').read_text()==smi
    log_iteration_results(db,project,1,0)
    inventory=[json.loads(line) for line in (root/'iteration01-structures.jsonl').read_text().splitlines()]
    outputs=[r for r in inventory if r['kind']=='result' and r['output_passed']]
    assert [r['display_name'] for r in outputs]==['01_0001','01_0001','01_0002']


def test_no_passing_compounds_exports_empty_smiles(project):
    db,_=stage.prepare_project(project)
    export_results(db,project['out-dir'])
    assert (Path(project['out-dir'])/'compounds.smi').read_text()==''
