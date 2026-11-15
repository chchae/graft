"""Worker-owned reference score publication and fresh-project cache reuse."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import tempfile
from types import SimpleNamespace

from graft import database
from graft.paths import stage_directory

SCHEMA = 'graft_reference_scores_v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native(args, action='inspect', request=None, source=None):
    from graft import _core
    with tempfile.TemporaryDirectory(prefix='graft-reference-scores-') as directory:
        root = Path(directory)
        inputs, output = root/'request.json', root/'result.json'
        inputs.write_text(json.dumps(request or {}, allow_nan=False))
        _core.run_command([args.schrodinger/'run', Path(__file__).with_name('reference_scores_native.py'),
                           action, source or args.reference_poses, inputs, output])
        return json.loads(output.read_text())


def context(args):
    """Scientific inputs/settings, excluding paths/CPU counts/deadlines of a run."""
    default = stage_directory(args.output, 0, 'prime_gbsa')/'receptor.maegz'
    amber_receptor = getattr(args, 'amber_receptor', default)
    prime_receptor = getattr(args, 'prime_gbsa_receptor', None)
    md_protocol = {key:getattr(args, 'md_gbsa_'+key) for key in
                   ('production_ns','equilibration_ns','snapshots','analysis_start_fraction','seed')}
    local_md = getattr(args, 'md_gbsa_local_compute', False)
    if local_md:
        md_protocol.update(prime_job_type='REAL_MIN', prime_flexdist=6.0, prime_flexgroup='residue')
    return dict(target=dict(grid_sha256=digest(args.grid),
                            amber_receptor_sha256=digest(amber_receptor) if amber_receptor != default else None,
                            prime_receptor_sha256=digest(prime_receptor) if prime_receptor else None),
                amber=dict(charge_method=args.amber_charge_method,
                           environment=args.amber_remote['remote_env'],
                           implementation=digest(Path(__file__).with_name('amber_runner.py'))),
                prime=dict(enabled=getattr(args,'prime_gbsa_enabled',True),
                           flexdist=getattr(args,'prime_gbsa_flexdist',0.0),
                           suite=(args.prime_gbsa_local_schrodinger if getattr(args,'prime_gbsa_local_compute',False)
                                  else str(args.schrodinger))),
                md=dict(protocol=md_protocol, replicates=args.md_gbsa_replicates,
                        suite=args.md_gbsa_schrodinger,
                        prime_suite=args.md_gbsa_local_schrodinger if local_md else args.md_gbsa_schrodinger,
                        implementation=digest(Path(__file__).with_name('md_gbsa_native.py')),
                        prime_implementation=digest(Path(__file__).with_name('md_gbsa_local_native.py')) if local_md else None,
                        input_count=args.md_gbsa_input_count, input_ratio=args.md_gbsa_input_ratio,
                        attraction_point=list(args.attraction_point) if getattr(args,'attraction_point',None) else None))


def validate_result(result, md):
    """A scalar or a partial/duplicate ensemble cannot establish MD completion."""
    if not isinstance(result, dict):
        raise ValueError('Missing cached MD ensemble')
    frames = result.get('snapshots', [])
    protocol, replicas = md['protocol'], md['replicates']
    expected = {(r, f) for r in range(1, replicas+1) for f in range(protocol['snapshots'])}
    if (not isinstance(frames,list) or not frames
            or not all(isinstance(f,dict) for f in frames)
            or result.get('protocol') != protocol or len(frames) != len(expected)
            or {(f.get('replica',1),f.get('frame')) for f in frames} != expected
            or not all(not isinstance(f.get('score'),bool) and isinstance(f.get('score'), (int,float))
                       and math.isfinite(f['score']) for f in frames)):
        raise ValueError('Invalid cached MD ensemble/protocol')
    if not all(not isinstance(result.get(key),bool) and isinstance(result.get(key),(int,float))
               and math.isfinite(result[key]) for key in ('mean','std')):
        raise ValueError('Invalid cached MD statistics')
    values = [f['score'] for f in frames]
    std = statistics.stdev(values) if len(values)>1 else 0.0
    if (not math.isclose(statistics.mean(values),result['mean'],abs_tol=1e-10)
            or not math.isclose(std,result['std'],abs_tol=1e-10)):
        raise ValueError('Cached MD statistics do not match snapshot energies')


def verified_upstream_context(args, expected, rows):
    """Do not label old scores with settings edited after their calculation."""
    from graft import amber_gbsa
    result = dict(expected)
    for stage in ('amber','prime'):
        needed = {r['id'] for r in rows if r[stage+'_gbsa_score'] is not None}
        covered = set()
        root = stage_directory(args.output,0,stage+'_gbsa')
        for path in root.glob('*.completed.json'):
            record = json.loads(path.read_text())
            if stage == 'amber':
                if (record.get('charge_method') != expected['amber']['charge_method']
                        or record.get('settings',{}).get('remote_env') != expected['amber']['environment']):
                    continue
                fingerprints = record.get('source_fingerprints',{})
                reference = str(args.reference_poses.resolve())
                receptors = [value for key,value in fingerprints.items() if key != reference]
                if reference not in fingerprints or len(receptors) != 1:
                    continue
                # A completed project directory may have been archived/renamed.
                # Verify the same source digests at their current local paths.
                rebased = dict(record, source_fingerprints={reference:fingerprints[reference],
                    str(args.amber_receptor.resolve()):receptors[0]})
                amber_gbsa.verify_sources(args,0,rebased)
            else:
                command = record.get('command',[])
                suite = str(Path(command[0]).parent) if command else record.get('schrodinger')
                flex = (float(command[command.index('-rflexdist')+1]) if '-rflexdist' in command else
                        record.get('prime_gbsa_flexdist',record.get('owner',{}).get('prime_gbsa_flexdist',0.0)))
                if suite != expected['prime']['suite'] or flex != expected['prime']['flexdist']:
                    continue
            covered.update(record.get('compound_ids',[]))
        if needed - covered:
            result[stage] = None  # Displayable energies, but no certified reuse context.
    return result


def publish(args):
    """Publish only iteration-0 records after its native MD work has resolved."""
    if not getattr(args, 'reference_poses', None) or not args.reference_poses.is_file():
        return 0
    selection = database.md_gbsa_selection(args.db_path, 0)
    if (selection is None or selection['settings'].get('reference_score_cache')
            or database.md_gbsa_completion_reason(args.db_path, 0) is not None):
        return 0
    rows = database.reference_score_records(args.db_path)
    complete = {r['id'] for r in rows if r['md_gbsa_score'] is not None
                and math.isfinite(r['md_gbsa_score']) and r['result'] is not None}
    if not selection['ids'] or not set(selection['ids']) <= complete:
        print('Reference score cache unchanged: selected MD ensembles include failed or missing results.', flush=True)
        return 0
    for stage in ('amber_gbsa','prime_gbsa'):
        if database.stage_is_running(args.db_path, stage):
            return 0
    if any((stage_directory(args.output,0,stage)/name).exists() for stage,name in
           (('amber_gbsa','amber_job.json'),('prime_gbsa','prime_gbsa_job.json'))):
        return 0
    expected = context(args)
    settings = selection['settings']
    if (settings['protocol'] != expected['md']['protocol']
            or settings['replicates'] != expected['md']['replicates']
            or settings['suite'] != expected['md']['suite']
            or settings.get('helper_sha256') != expected['md']['implementation']
            or settings.get('local_compute',{}).get('suite',settings['suite']) != expected['md']['prime_suite']):
        print('Reference score cache not published: saved MD protocol differs from current settings.', flush=True)
        return 0
    source = native(args)
    expected = verified_upstream_context(args, expected, rows)
    by_name = {r['name']:r for r in rows}
    names = {r['id']:r['name'] for r in rows}
    selected_names = [names[cid] for cid in selection['ids']]
    updates = {}
    seen = set()
    for record in source['records']:
        if record['smiles'] is None:
            continue
        row = by_name.get(record['name'])
        if row is None or row['smiles'] != record['smiles'] or row['name'] in seen:
            raise ValueError('Reference identity does not match SQLite; score export stopped')
        seen.add(row['name'])
        scores = {key: row[key+'_score'] if row[key+'_score'] is not None and math.isfinite(row[key+'_score']) else None
                  for key in ('amber_gbsa','prime_gbsa','md_gbsa')}
        result = row['result'] if scores['md_gbsa'] is not None else None
        if result is not None:
            validate_result(result, expected['md'])
            if result.get('id') != row['id'] or result['mean'] != scores['md_gbsa']:
                raise ValueError('Reference MD cache score/result mismatch')
        updates[record['index']] = dict(schema=SCHEMA, context=expected,
            pose_signature=record['signature'], source_signature=source['signature'],
            md_selection=selected_names, scores=scores, md_result=result)
    if seen != set(by_name):
        raise ValueError('Missing SQLite reference in reference-pose file')
    native(args, 'update', dict(sha256=source['sha256'], updates=updates))
    print(f'Reference scores saved to {args.reference_poses}: {len(updates)} structures.', flush=True)
    return len(updates)


def restore(args):
    """Fresh initialization only; never fill or reset live/existing projects."""
    source = native(args)
    expected = context(args)
    records = {}
    for row in source['records']:
        if not row['cache']:
            continue
        try:
            cache = json.loads(row['cache'])
            if (not isinstance(cache,dict) or cache.get('schema') != SCHEMA
                    or not isinstance(cache.get('context'),dict)
                    or not isinstance(cache.get('scores'),dict)
                    or any(key not in cache['context'] for key in ('target','amber','prime','md'))
                    or any(key not in cache['scores'] for key in ('amber_gbsa','prime_gbsa','md_gbsa'))
                    or cache['scores'] != row.get('scores')
                    or any(value is not None and (isinstance(value,bool) or not isinstance(value,(int,float))
                               or not math.isfinite(value)) for value in cache['scores'].values())
                    or cache['pose_signature'] != row['signature']
                    or cache['source_signature'] != source['signature']
                    or cache['context']['target'] != expected['target']):
                continue
            records[row['name']] = dict(cache, smiles=row['smiles'])
        except (ValueError, KeyError, TypeError):
            continue
    if not records:
        database.finish_reference_cache_import(args.db_path)
        return 0
    # Amber can be reused independently. With MD enabled, Prime reuse requires
    # the whole requested MD selection, since cached scores contain no optimized
    # Prime complexes from which to start a different MD protocol.
    md = None
    if getattr(args, 'md_gbsa_enabled', False):
        first = next(iter(records.values()))
        names = first.get('md_selection',[])
        population = [r['name'] for r in source['records'] if r['smiles'] is not None]
        requested = args.md_gbsa_input_count
        quota = min(len(population), requested if requested is not None else max(1,math.ceil(len(population)*args.md_gbsa_input_ratio)))
        chain_complete = all(name in records and records[name]['context'] == expected and
            all(not getattr(args,key+'_enabled',True) or
                (isinstance(records[name]['scores'].get(key),(int,float))
                 and not isinstance(records[name]['scores'].get(key),bool)
                 and math.isfinite(records[name]['scores'][key]))
                for key in ('amber_gbsa','prime_gbsa')) for name in population)
        if (isinstance(names,list) and names and all(isinstance(n,str) for n in names)
                and len(names)==quota and len(set(names))==len(names) and chain_complete):
            try:
                for name in names:
                    item = records[name]
                    if (item['context'] != expected or item['md_selection'] != names
                            or item['scores']['md_gbsa'] != item['md_result']['mean']):
                        raise ValueError('Reference cache calculation settings differ')
                    validate_result(item['md_result'], expected['md'])
                    native_record = next(r for r in source['records'] if r['name']==name)
                    if (native_record['md_std'] != item['md_result']['std']
                            or native_record['md_snapshots'] != len(item['md_result']['snapshots'])):
                        raise ValueError('Cached MD properties disagree with ensemble')
                md = dict(names=names, settings=dict(reference_score_cache=True,
                    protocol=expected['md']['protocol'], replicates=expected['md']['replicates'],
                    ranking_source='reference_score_cache'), results={n:records[n]['md_result'] for n in names})
                md['settings']['reference_score_cache'] = dict(schema=SCHEMA,
                    source=str(args.reference_poses.resolve()), source_sha256=source['sha256'],
                    source_signature=source['signature'], context=expected)
            except (ValueError, KeyError, TypeError):
                md = None
    updates = {}
    for name, item in records.items():
        scores = {}
        if item['context']['amber'] == expected['amber']:
            scores['amber_gbsa_score'] = item['scores']['amber_gbsa']
        if (item['context']['prime'] == expected['prime']
                and (not getattr(args,'md_gbsa_enabled',False) or md is not None)):
            scores['prime_gbsa_score'] = item['scores']['prime_gbsa']
        updates[name] = dict(smiles=item['smiles'], scores=scores)
    count = database.restore_reference_score_records(args.db_path, updates, md)
    print(f'Reference score cache reused: {count} structures; MD ensemble cache={md is not None}.', flush=True)
    return count


def annotation_matches(args, expected_sha256):
    """Allow verified score-only annotations when recovering old source hashes."""
    path = args.reference_poses
    backup = path.with_name(path.name+'.graft-backup-'+expected_sha256+path.suffix)
    if not backup.is_file() or digest(backup) != expected_sha256:
        return False
    return native(args, source=backup)['signature'] == native(args)['signature']


def preserve_parent_signature(current, saved):
    """Retain a frozen input export only for proven reference-score annotations."""
    candidate = dict(current, sources=dict(current['sources']))
    before = saved.get('sources',{}).get('parents')
    after = candidate['sources'].get('parents')
    if before and after and before['path'] == after['path']:
        candidate['sources']['parents'] = before
        args = SimpleNamespace(reference_poses=Path(after['path']),schrodinger=Path(current['suite']))
        if candidate == saved and annotation_matches(args,before['sha256']):
            return saved
    raise RuntimeError('Native input sources changed; existing cache retained. Use a new 3D output directory.')
