"""Read published ligand poses and freeze a soft spatial selection preference."""
import hashlib
import json
import math
from pathlib import Path
import re
import sys


def measure(request, structure):
    """Use the saved best Glide pose, never select a different LigPrep state."""
    point = request['point']
    wanted = {int(cid): score for cid, name, score in request['rows']}
    names = {name: int(cid) for cid, name, score in request['rows']}
    distances = {}
    for index, st in enumerate(structure.StructureReader(request['source']), 1):
        if request['iteration'] == 0:
            title = (st.title or '').strip() or f'REF_{index:04d}'
            cid = names.get(title)
        else:
            match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
            cid = int(match[1]) if match else st.property.get('i_user_compound_id')
        if cid not in wanted:
            continue  # Shared receptor and unselected identities are not ligands.
        if str(cid) in distances:
            raise ValueError(f'Duplicate published pose for CMPID_{cid}')
        if request['iteration'] > 0:
            score = float(st.property['r_i_docking_score'])
            if not math.isfinite(score) or not math.isclose(score,wanted[cid],rel_tol=1e-9,abs_tol=1e-6):
                raise ValueError(f'Published Glide pose/score differs for CMPID_{cid}')
        squared = []
        for atom in st.atom:
            if atom.atomic_number <= 1:
                continue
            xyz = (atom.x,atom.y,atom.z)
            if not all(math.isfinite(x) for x in xyz):
                raise ValueError(f'Nonfinite pose coordinates for CMPID_{cid}')
            squared.append(sum((x-y)**2 for x,y in zip(xyz,point)))
        if not squared:
            raise ValueError(f'No heavy atoms in CMPID_{cid}')
        distances[str(cid)] = math.sqrt(min(squared))
    missing = set(wanted)-{int(cid) for cid in distances}
    if missing:
        raise ValueError(f'Missing published ligand poses: {sorted(missing)}')
    return distances


def prepare(args, iteration, stage):
    """Worker-only geometry I/O; the driver and candidate SQL never read poses."""
    from graft import database
    from graft.paths import stage_directory
    point = getattr(args,'attraction_point',None)
    if point is None:
        return
    saved = database.attraction_policy(args.db_path,iteration,stage)
    if saved is not None:
        if saved['point'] is not None and saved['source']:
            if hashlib.sha256(Path(saved['source']).read_bytes()).hexdigest() != saved['source_sha256']:
                raise RuntimeError('Published poses changed after attraction_point was frozen')
        if saved['point'] is not None and list(point) != saved['point']:
            print(f'Iteration {iteration}: {stage} retains its frozen attraction_point={saved["point"]}.',flush=True)
        return saved  # Existing stage choices are immutable, including legacy ones.
    if database.attraction_stage_started(args.db_path,iteration,stage):
        print(f'Iteration {iteration}: {stage} retains its existing selection; attraction_point applies to new selections.',flush=True)
        return database.freeze_attraction_policy(args.db_path,iteration,stage,None,{})
    if iteration > 0:
        reason = database.docking_completion_reason(args.db_path,iteration,args.target_count)
        if reason:
            raise RuntimeError('attraction_point waiting: '+reason)
    rows = database.attraction_input_rows(args.db_path,iteration)
    source = (Path(args.reference_poses) if iteration == 0 else
              stage_directory(args.output,iteration,'glide')/'best_poses.maegz')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = stage_directory(args.output,iteration,stage)/'attraction_point'
    folder.mkdir(parents=True,exist_ok=True)
    request = dict(point=point,rows=rows,source=str(source.resolve()),iteration=iteration)
    request_file = folder/'request.json'
    request_file.write_text(json.dumps(request,allow_nan=False))
    result_file = folder/'distances.json'
    result_file.unlink(missing_ok=True)
    from graft import _core
    _core.run_command([args.schrodinger/'run',Path(__file__).resolve(),request_file,result_file],cwd=folder)
    distances = json.loads(result_file.read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Published poses changed during attraction_point preparation')
    saved = database.freeze_attraction_policy(args.db_path,iteration,stage,point,distances,
                                              str(source.resolve()),digest,rows)
    hits = sum(distance <= 2.0 for distance in saved['distances'].values())
    print(f'Iteration {iteration}: {stage} attraction_point priority: {hits}/{len(rows)} ligand poses within 2 A.',flush=True)
    return saved


if __name__ == '__main__':
    from schrodinger import structure
    request = json.loads(Path(sys.argv[1]).read_text())
    result = measure(request,structure)
    Path(sys.argv[2]).write_text(json.dumps(result,allow_nan=False,sort_keys=True))
