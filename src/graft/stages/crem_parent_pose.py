"""Freeze selected parent docking poses before a new CReM generation plan."""
import hashlib
import json
from pathlib import Path

from rdkit import Chem

from graft import _core, database
from graft.paths import stage_directory
from graft.chemistry.combichem3d.parent_pose import constrain_parent


def bind_parents(args, iteration, folder, parents):
    source = stage_directory(args.output, iteration - 1, 'glide') / 'best_poses.maegz'
    if not source.is_file():
        raise RuntimeError('Selected elite best Glide pose file is missing; no reference fallback')
    scores = database.crem_parent_docking_scores(args.db_path, iteration - 1,
                                               [p['compound_id'] for p in parents])
    output = folder / 'parent_docking_poses.sdf'
    request = folder / 'parent_docking_request.json'
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    request.write_text(json.dumps(dict(source=str(source.resolve()), output=str(output.resolve()), scores=scores)))
    helper = Path(__file__).resolve().parents[1] / 'crem_docking_poses.py'
    _core.run_command([args.schrodinger / 'run', helper, request.resolve()], cwd=folder)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Elite Glide pose source changed while preparing references')
    poses = {}
    for mol in Chem.SDMolSupplier(str(output), removeHs=False):
        if mol is None:
            raise ValueError('Invalid elite docking SDF')
        cid = int(mol.GetProp('_Name').removeprefix('CMPID_'))
        if cid not in scores or cid in poses:
            raise ValueError('Unexpected or duplicate elite docking SDF identity')
        poses[cid] = mol
    if set(poses) != set(scores):
        raise ValueError('Incomplete elite docking SDF')
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    bound = []
    for parent in parents:
        row = constrain_parent(parent, poses[parent['compound_id']], timeout=args.mcs_timeout)
        row['docking_reference'].update(source=str(output.resolve()), sha256=checksum,
            source_glide_sha256=source_hash, docking_score=scores[parent['compound_id']])
        row['history']['constraint_reference'] = dict(row['docking_reference'])
        bound.append(row)
    return bound
