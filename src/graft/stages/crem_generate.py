"""One fixed-frame CReM expansion per scored GRAFT iteration, without internal cycles."""
import hashlib
import json
from pathlib import Path

from rdkit import Chem

from graft import database
from graft.config import combichem_3d_settings
from graft.paths import stage_directory
from graft.states import GrowthTaskState as Growth
from graft.stages._logging import work_started
from graft.stages.generate import prepare_workspace
from graft.chemistry.combichem3d import frames, history as provenance
from graft.chemistry.combichem3d.crem_growth import validate_database
from graft.chemistry.combichem3d.inputs import materialize_native_inputs
from graft.chemistry.combichem3d.logging_utils import configure
from graft.stages import combichem_3d as growth


def settings(args, iteration, folder):
    cfg = combichem_3d_settings(args.toml_file)
    if cfg['generation-backend'] != 'crem':
        raise ValueError('Set [combichem-3d].generation-backend="crem"')
    if cfg.get('execution-host'):
        raise ValueError('Integrated CReM generation currently requires local workers; use local processes')
    # Rebase all generated caches before native extraction. Never touch standalone output.
    cfg.update({'out-dir':str(folder), 'history':str(folder/'input-cache/history.json'),
                'history-source':'pipeline', 'reference-smiles':str(folder/'input-cache/references.smi'),
                'reference-source-db':str(args.db_path), 'schrodinger':str(args.schrodinger),
                'generation-backend':'crem', 'growth-mode':'fixed-frame', 'filters':{},
                'prune-intermediates':False, 'max-iteration':iteration, 'stop-mode':'target',
                'target-count':args.target_count, 'pipeline-generation':True})
    return cfg


def _fingerprint(cfg, parents):
    operational = {'config-path','out-dir','history','reference-smiles','reference-source-db',
                   'max-seconds','processes','batch-size','result-buffer-size','result-buffer-seconds',
                   'grace-seconds','log-level','log-interval','log-structures'}
    frozen = {k:v for k,v in cfg.items() if k not in operational}
    frozen['files'] = {key:growth.file_sha256(cfg[key]) for key in ('parents','receptor','crem-db')}
    wal = Path(cfg['crem-db']+'-wal')
    frozen['crem-wal'] = growth.file_sha256(wal) if wal.exists() and wal.stat().st_size else None
    source = Path(growth.__file__).resolve().parents[1]/'chemistry/combichem3d'
    frozen['implementation'] = {p.name:growth.file_sha256(p) for p in sorted(source.glob('*.py'))}
    for filename in ('crem_generate.py','combichem_3d.py','combichem_product.py','combichem_buffer.py'):
        frozen['implementation'][filename] = growth.file_sha256(Path(__file__).with_name(filename))
    return hashlib.sha256(json.dumps(dict(settings=frozen,parents=parents),sort_keys=True).encode()).hexdigest()


def _ready(args, iteration):
    from graft.driver import reference_work_pending, external_completion_reason
    if reference_work_pending(args):
        return 'iteration 0 calculations are incomplete'
    for previous in range(1, iteration):
        reason = database.generation_completion_reason(args, previous, 0)
        if reason:
            return f'iteration {previous}: {reason}'
        for stage in ('glide','prime_gbsa'):
            reason = external_completion_reason(args, previous, stage)
            if reason:
                return f'iteration {previous}: {reason}'
        if database.stage_is_running(args.db_path,'amber_gbsa'):
            return 'Amber worker is still running'
        reason = database.md_gbsa_completion_reason(args.db_path, previous)
        if reason:
            return f'iteration {previous}: {reason}'
    return None


def run_stage(args, requested_iteration):
    if requested_iteration is None:
        from graft.driver import choose_iteration
        iteration = choose_iteration(args)
    else:
        iteration = requested_iteration
    if iteration is None:
        print('All configured iterations are complete.')
        return
    if not 1 <= iteration <= args.max_iteration:
        raise ValueError('CReM generation requires a configured iteration >= 1')
    saved = database.crem_generation(args.db_path, iteration)
    if saved and saved['state'] == Growth.DONE:
        database.validate_generation_population(args.db_path, iteration, args.target_count)
        print(f'Iteration {iteration}: retaining completed CReM population ({saved["population_count"]} compounds).')
        return
    if saved is None and database.iteration_has_compounds(args.db_path, iteration):
        raise RuntimeError('Cannot convert an existing population to CReM; use a new project output')
    reason = _ready(args, iteration)
    if reason:
        print(f'Iteration {iteration}: CReM generation waiting: {reason}.')
        return
    if not prepare_workspace(args, iteration):
        return
    folder = stage_directory(args.output, iteration, 'reinvent')/'crem'
    folder.mkdir(parents=True, exist_ok=True)
    cfg = settings(args, iteration, folder)
    configure(folder, cfg['log-level'])
    materialize_native_inputs(cfg)
    cfg['runtime-versions'] = growth.versions() | {'crem':validate_database(cfg)}
    refs = database.growth_project_references(args.db_path)
    Path(cfg['reference-smiles']).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg['reference-smiles']).write_text(''.join(f'{s}\tCMPID_{cid}\n' for cid,s in refs))
    if saved:
        parents = saved['plan']['parents']
    elif iteration == 1:
        mol, history = frames.initial_frame(cfg['parents'], cfg['reference-mcs'], growth_mode='fixed-frame')
        history['lineage_id'] = hashlib.sha256(json.dumps(history,sort_keys=True).encode()).hexdigest()
        parents = [dict(smiles=provenance.canonical_smiles(mol),molblock=Chem.MolToMolBlock(mol),history=history)]
    else:
        if database.crem_generation(args.db_path, iteration-1) is None:
            raise RuntimeError('Preceding iteration has no CReM provenance; use a new project output')
        parents = database.crem_parent_poses(args.db_path, iteration-1, args.elite_count)
        if not parents:
            raise RuntimeError('No finite MD-GBSA elites with saved CReM provenance; generation stopped')
    fingerprint = _fingerprint(cfg, parents)
    plan = dict(protocol='crem_pipeline_single_expansion_v1',target_count=args.target_count,
                elite_count=args.elite_count,score_source='md_gbsa',parents=parents,fingerprint=fingerprint)
    database.freeze_crem_generation(args.db_path, iteration, plan)
    _save_scaffold(args, iteration, cfg, parents)
    db = folder/'combichem3d.sqlite'
    work_started(f'Iteration {iteration}: CReM single expansion from {len(parents)} parent(s); '
                 f'target={args.target_count} novel compounds before drug likeness.')
    with database.stage_lock(db,'combichem3d'):
        # SQLite coordination and durable journals are reused, but no inner cycle selection occurs.
        excluded = [s for _,s in database.project_smiles(args.db_path)]
        database.growth_initialize(db, cfg, fingerprint, parents,
                                  external_iteration=iteration,excluded_smiles=excluded)
        growth.supervise(cfg, db, iteration)
        info = database.growth_run_info(db)
        if info['stop_reason'] not in ('target_count','exhausted'):
            raise RuntimeError(f'CReM generation remains incomplete ({info["stop_reason"]}); saved work retained')
        if info['stop_reason']=='exhausted' and info['pending_plans']:
            raise RuntimeError('CReM has unfinished plans; saved work retained')
        records = database.growth_pipeline_results(db, iteration)
        count = database.import_crem_results(args.db_path, iteration, records, info['stop_reason'])
    print(f'Iteration {iteration}: CReM generation complete, {count}/{args.target_count} novel compounds; '
          f'reason={info["stop_reason"]}. Continuing with drug likeness.', flush=True)


def _save_scaffold(args, iteration, cfg, parents):
    from graft.chemistry.scaffold import _publish
    query,_ = frames.frame_query(cfg['reference-mcs'])
    core = Chem.MolToSmarts(query)
    frame,_ = frames.initial_frame(cfg['parents'],cfg['reference-mcs'],growth_mode='fixed-frame')
    snapshot = dict(generation_method='crem',source='fixed_crem_frame',
                    source_ids=[p['compound_id'] for p in parents if 'compound_id' in p],
                    mcs_smarts=core,docking_mcs_smarts=core,
                    scaffold_smiles=provenance.canonical_smiles(frame))
    initial = database.iteration_scaffold(args.db_path,0,dict(snapshot,source='user_initial_mcs',source_ids=[]))
    saved = database.iteration_scaffold(args.db_path,iteration,snapshot)
    if initial['mcs_smarts'] != core or saved != snapshot:
        raise RuntimeError('Existing docking/scaffold snapshot conflicts with the fixed CReM frame; results retained')
    directory=stage_directory(args.output,iteration,'scaffold')
    _publish(directory/'docking_mcs.smarts',core+'\n')
    _publish(directory/'scaffold_info.json',json.dumps(snapshot,indent=2)+'\n')
