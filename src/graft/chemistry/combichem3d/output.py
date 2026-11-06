"""Rebuild bounded, atomic output artifacts from durable SQLite results."""
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import tempfile
import numpy as np
from rdkit import Chem, rdBase
from graft import database
from graft.states import GrowthTaskState as Growth


def versions():
    return dict(python=platform.python_version(), rdkit=rdBase.rdkitVersion,
                numpy=np.__version__, scipy=importlib.metadata.version('scipy'),
                graft=importlib.metadata.version('graft'))


def rows(path, table, batch):
    after = 0
    while True:
        chunk = database.growth_rows(path,table,after,batch)
        if not chunk:
            break
        yield from chunk
        after = chunk[-1]['export_rowid']


def passing_names(db, iteration=None):
    """Display labels only; internal CMPID identities and pose lineage stay intact."""
    result={}
    current=None
    number=0
    for row in database.growth_output_compounds(db,iteration):
        if row['iteration']!=current:
            current=row['iteration'];number=0
        number+=1
        result[(current,row['compound_id'])]=f'{current:02d}_{number:04d}'
    return result


def pose_molecule(row, display_name=None, crem_mode=False):
    """Restore one saved pose and the metadata shared by all SDF exports."""
    metadata = json.loads(row['history'])
    mol = Chem.MolFromMolBlock(row['molblock'],removeHs=False)
    if mol is None:
        raise RuntimeError(f"Cannot reconstruct saved pose {row['id']}")
    from graft.chemistry.combichem3d.history import restore
    restore(mol,metadata)
    Chem.CreateAtomIntPropertyList(mol,'graft_added_cycle')
    mol.SetProp('_Name',f"CMPID_{row['compound_id']}")
    if crem_mode:
        mol.SetProp('cmpid',f"CMPID_{row['compound_id']}")
    if display_name:
        mol.SetProp('_Name',display_name)
        mol.SetProp('display_name',display_name)
    for key,value in dict(pose_id=row['id'],parent_pose=row['parent_pose'],iteration=row['iteration'],
                          output_passed=bool(row['output_passed']),history=metadata,
                          properties=json.loads(row['properties'] or '{}')).items():
        mol.SetProp(key,json.dumps(value,sort_keys=True))
    return mol


def export_cycle_results(db, output, iteration):
    """Publish passing CReM identities and all their saved poses for one cycle."""
    root = Path(output)
    root.mkdir(parents=True,exist_ok=True)
    labels = passing_names(db,iteration)
    stem = f'cycle{iteration:02d}'
    with tempfile.TemporaryDirectory(prefix='.export-cycle-',dir=root) as directory:
        temporary = Path(directory)
        with (temporary/f'{stem}.smi').open('w') as stream:
            for row in database.growth_output_compounds(db,iteration):
                stream.write(f"{row['smiles']}\t{labels[(iteration,row['compound_id'])]}\n")
        with Chem.SDWriter(str(temporary/f'{stem}.sdf')) as writer:
            for row in database.growth_cycle_output_poses(db,iteration):
                writer.write(pose_molecule(row,labels[(iteration,row['compound_id'])],True))
        for suffix in ('smi','sdf'):
            os.replace(temporary/f'{stem}.{suffix}',root/f'{stem}.{suffix}')


def export_results(db, output, batch=256):
    root = Path(output)
    root.mkdir(parents=True,exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.export-',dir=root))
    info = database.growth_run_info(db)
    crem_mode=info['settings'].get('generation-backend')=='crem'
    display_names=passing_names(db) if crem_mode else {}
    cycle_iterations={iteration for iteration,_ in display_names}
    names = ['probes.jsonl','failures.jsonl','growth_history.jsonl','compounds.csv','poses.sdf','passed_poses.sdf','summary.json']
    try:
        if crem_mode:
            names.append('compounds.smi')
            with (temporary/'compounds.smi').open('w') as stream:
                for row in database.growth_output_compounds(db):
                    name=display_names[(row['iteration'],row['compound_id'])]
                    stream.write(f"{row['smiles']}\t{name}\n")
        if info['settings'].get('growth-mode') in ('frame-cycles','fixed-frame'):
            names.append('cycles.jsonl')
            with (temporary/'cycles.jsonl').open('w') as stream:
                for row in rows(db,'combichem3d_cycle',batch):
                    if row['iteration'] > 0:
                        cycle_iterations.add(row['iteration'])
                    stream.write(json.dumps(dict(iteration=row['iteration'],state=row['state'],frame_pose=row['frame_pose'],
                                                 **json.loads(row['report'])))+'\n')
        with (temporary/'probes.jsonl').open('w') as probes, (temporary/'failures.jsonl').open('w') as failures:
            for row in rows(db,'combichem3d_probe',batch):
                report = json.loads(row['report'])
                item = dict(parent_pose=row['parent_pose'],site=row['site'],**report)
                probes.write(json.dumps(item)+'\n')
                if report.get('status') in ('UNRESOLVED','CLASH_REJECT'):
                    failures.write(json.dumps(dict(kind='probe',**item))+'\n')
            for row in rows(db,'combichem3d_attempt',batch):
                report = json.loads(row['report'])
                if row['status'] != 'PASS' or not report.get('output_passed',True):
                    failures.write(json.dumps(dict(kind='product',parent_pose=row['parent_pose'],ordinal=row['ordinal'],
                                                   failed_stage=row['failed_stage'],failure_message=row['failure_message'],**report))+'\n')
            for row in rows(db,'combichem3d_batch',batch):
                if int(row['cursor']) < int(row['stop']):
                    failures.write(json.dumps(dict(kind='deferred_batch',status='UNRESOLVED',
                        parent_pose=row['parent_pose'],cursor=row['cursor'],stop=row['stop'],
                        reason=info['stop_reason']))+'\n')
            for row in rows(db,'combichem3d_event',batch):
                failures.write(json.dumps(dict(kind=row['kind'],created=row['created'],**json.loads(row['report'])))+'\n')
        all_poses, passed_poses = Chem.SDWriter(str(temporary/'poses.sdf')), Chem.SDWriter(str(temporary/'passed_poses.sdf'))
        try:
            with (temporary/'growth_history.jsonl').open('w') as history, (temporary/'failures.jsonl').open('a') as failures:
                for row in rows(db,'combichem3d_pose',batch):
                    metadata = json.loads(row['history'])
                    unfinished = (row['plan_state'] in (Growth.PENDING,Growth.RUNNING)
                                  or (row['plan_state']==Growth.READY and int(row['allocated'])<int(row['total'])))
                    if row['input_state']!=Growth.DONE or (row['iteration'] < info['settings']['max-iteration'] and unfinished):
                        failures.write(json.dumps(dict(kind='deferred_parent',status='UNRESOLVED',
                            parent_pose=row['id'],allocated=row['allocated'],total=row['total'],reason=info['stop_reason']))+'\n')
                    display_name=display_names.get((row['iteration'],row['compound_id'])) if row['output_passed'] else None
                    mol = pose_molecule(row,display_name,crem_mode)
                    all_poses.write(mol)
                    if row['output_passed']:
                        passed_poses.write(mol)
                    history.write(json.dumps(dict(pose_id=row['id'],compound_id=row['compound_id'],
                                                   parent_pose=row['parent_pose'],output_passed=bool(row['output_passed']),
                                                   **(dict(display_name=display_name) if display_name else {}),**metadata))+'\n')
        finally:
            all_poses.close(); passed_poses.close()
        metrics = ['mw','clogp','tpsa','qed','similarity','hbd','hba','rotatable_bonds','heavy_atoms']
        with (temporary/'compounds.csv').open('w',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=['id','name',*(['cmpid'] if crem_mode else []),'smiles','first_iteration','iterations','pose_count',*metrics])
            writer.writeheader()
            after = 0
            while True:
                chunk = database.growth_compound_rows(db,after,batch)
                if not chunk:
                    break
                for row in chunk:
                    props = json.loads(row.pop('properties'))
                    if crem_mode:
                        row['cmpid']=row['name']
                        row['name']=display_names[(row['first_iteration'],row['id'])]
                    writer.writerow(row | {key:props.get(key) for key in metrics})
                after = chunk[-1]['id']
        info['versions'] = info['settings'].get('runtime-versions',versions())
        info['export_versions'] = versions()
        frame_mode=info['settings'].get('growth-mode')=='frame-cycles'
        info['protocol'] = 'combichem3d_frame_cycles_v1' if frame_mode else 'combichem3d_v1'
        info['seed'] = info['settings']['seed']
        info['partial_search'] = (info['stop_reason'] not in ('exhausted','attachment_cycle_limits_reached','crem_search_exhausted') or info['statuses'].get('UNRESOLVED',0)>0
                                  or info['probe_statuses'].get('UNRESOLVED',0)>0
                                  or info['attempted'] < int(info['planned_combinations']) or info['pending_plans'] > 0)
        info['enumeration'] = 'Iteration 1: full core-attachment Cartesian products; later: full PASS-site candidate products conditional on finite probe searches'
        if frame_mode:
            info['enumeration']='Reference-MCS K^N; later elite-MCS frames with initial-branch-balanced, cycle-limit-eligible probe-PASS sites ranked by bond distance within each branch, full candidate Cartesian products'
            info['reference_poses_are_growth_parents']=False
        if info['settings'].get('generation-backend')=='crem':
            info['protocol']='combichem3d_crem_fixed_frame_v1'
            info['enumeration']='Single CReM addition per child at each methyl-PASS heavy-atom site across active branches; bounded context-conditioned pools and ranked previous-cycle parents'
            info['reference_poses_are_growth_parents']=False
        info['count_limit_active']=info['settings']['stop-mode']=='target'
        info['target_overshoot'] = max(0,info['unique_count']-info['settings']['target-count']) if info['count_limit_active'] else 0
        info['interpretation'] = 'MCS-constrained sidechain conformational search + receptor clash filtering; no docking/affinity claim'
        (temporary/'summary.json').write_text(json.dumps(info,indent=2,sort_keys=True)+'\n')
        if crem_mode:
            for iteration in sorted(cycle_iterations):
                export_cycle_results(db,temporary,iteration)
                names.extend([f'cycle{iteration:02d}.smi',f'cycle{iteration:02d}.sdf'])
        for name in names:
            os.replace(temporary/name,root/name)
        return info
    finally:
        for path in temporary.iterdir():
            path.unlink()
        temporary.rmdir()


def log_frames(db, source_iteration, iteration, limit=3, frames_only=False):
    from graft.chemistry.combichem3d.logging_utils import logger
    cfg=database.growth_run_info(db)['settings']
    display_names=passing_names(db,source_iteration) if cfg.get('generation-backend')=='crem' else {}
    logger.info('iteration %s input frames: source_iteration=%s (structure preview, at most %s)',iteration,source_iteration,limit)
    for row in database.growth_structure_rows(db,source_iteration,limit=limit,frames_only=frames_only):
        history=json.loads(row['history'])
        name=display_names.get((source_iteration,row['compound_id']),f"CMPID_{row['compound_id']}")
        logger.info('FRAME %s pose=%s source=%s SMILES=%s core=%s',name,row['pose_id'],
                    history.get('source_name','generated'),row['smiles'],history['core_smarts'])


def log_iteration_results(db, cfg, iteration, source_iteration):
    """Bounded inventories of input frames and saved geometric products."""
    from graft.chemistry.combichem3d.logging_utils import logger
    frame_mode=cfg.get('growth-mode')=='frame-cycles'
    counts=database.growth_iteration_counts(db,iteration,source_iteration,frame_mode)
    reason=database.growth_run_info(db)['stop_reason']
    counts.update(source_iteration=source_iteration,partial=bool(reason),stop_reason=reason)
    destination=Path(cfg['out-dir'])/f'iteration{iteration:02d}-structures.jsonl'
    temporary=destination.with_suffix('.jsonl.tmp')
    previews=[]
    seen=set()
    display_names=passing_names(db,iteration) if cfg.get('generation-backend')=='crem' else {}
    if cfg.get('generation-backend')=='crem' and source_iteration!=iteration:
        display_names.update(passing_names(db,source_iteration))
    with temporary.open('w') as stream:
        stream.write(json.dumps(dict(kind='summary',**counts))+'\n')
        for kind,number,results in (('frame',source_iteration,False),('result',iteration,True)):
            after=0
            while True:
                chunk=database.growth_structure_rows(db,number,after,cfg['batch-size'],results,frame_mode and kind=='frame')
                if not chunk:break
                for row in chunk:
                    history=json.loads(row.pop('history') or '{}')
                    if kind=='frame':
                        row['core_smarts']=history['core_smarts']
                        row['frame_smarts']=history.get('frame_smarts',history['core_smarts'])
                        row['attachments']=history['attachments']
                    if row['output_passed'] and (number,row['compound_id']) in display_names:
                        row['display_name']=display_names[(number,row['compound_id'])]
                    stream.write(json.dumps(dict(kind=kind,iteration=number,**row))+'\n')
                    if (kind=='result' and row['output_passed'] and row['compound_id'] not in seen
                            and len(previews)<cfg['log-structures']):
                        seen.add(row['compound_id']);previews.append(row)
                after=chunk[-1]['pose_id']
    os.replace(temporary,destination)
    if cfg.get('generation-backend')=='crem' and iteration > 0:
        export_cycle_results(db,cfg['out-dir'],iteration)
    statuses=counts['statuses']
    logger.info('iteration %s result: frames=%s attempted=%s passed_unique=%s passed_poses=%s CLASH_REJECT=%s UNRESOLVED=%s partial=%s',
                iteration,counts['frames'],counts['attempted'],counts['passed_unique'],counts['passed_poses'],
                statuses.get('CLASH_REJECT',0),statuses.get('UNRESOLVED',0),counts['partial'])
    for row in previews:
        logger.info('RESULT %s pose=%s parent_pose=%s SMILES=%s',row.get('display_name',f"CMPID_{row['compound_id']}"),row['pose_id'],row['parent_pose'],row['smiles'])
    pose_file = f'cycle{iteration:02d}.sdf' if cfg.get('pipeline-generation') else 'poses.sdf'
    logger.info('iteration %s complete structure inventory: %s; final 3D poses: %s',iteration,destination,Path(cfg['out-dir'])/pose_file)
    return counts
