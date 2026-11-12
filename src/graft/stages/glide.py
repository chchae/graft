#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from graft.paths import stage_directory
from graft.stages._logging import detail, progress
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from queue import Empty, Queue
from threading import Event
from graft import _core as c
from graft import database
from graft.glide_timing import record as record_timing
from graft.config import glide_group_resources, glide_job_options, glide_molecules_per_job
from graft.stages._logging import work_started, timed_stage, format_elapsed, current_time
from graft.states import CompoundState as State
from graft.glide_recovery import group_status, load_record, record_file, discard_terminal_results
from graft.glide_recovery import timeout_pose_files, save_record, output_paths, has_timeouts
from graft.schrodinger_guard import active_jobs, belongs_to_directory, SubmissionBlocked, job_details, TERMINAL


def pose_selection_point(args, iteration):
    return database.freeze_glide_pose_policy(args.db_path, iteration, getattr(args, 'attraction_point', None))


def iteration_batch_size(db_path, iteration):
    """Use stored Strict RotB; calculate missing legacy descriptors without DB edits."""
    rows = database.glide_input_rotatable_bonds(db_path, iteration)
    if not rows:
        raise RuntimeError(f'Iteration {iteration}: no Glide input population for RotB average')
    values = []
    for cid, smiles, value in rows:
        if value is None:
            from rdkit import Chem
            from rdkit.Chem import rdMolDescriptors
            mol = Chem.MolFromSmiles(smiles)
            if mol is None or not mol.GetNumAtoms():
                raise ValueError(f'Cannot calculate Glide RotB for CMPID_{cid}')
            value = rdMolDescriptors.CalcNumRotatableBonds(
                mol, rdMolDescriptors.NumRotatableBondsOptions.Strict)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f'Invalid Glide RotB for CMPID_{cid}: {value}')
        values.append(value)
    average = math.fsum(values) / len(values)
    size = glide_molecules_per_job(average)
    print(f'Iteration {iteration}: Glide mean RotB={average:.4f} '
          f'({len(values)} original compounds); target={size} prepared structures/subjob '
          '(floor(75 - 2.5 * mean RotB), minimum 1).', flush=True)
    return size


def prepare_new_inputs(args, entries, glide_root):
    """Batch only unsubmitted groups; never rewrite saved native job inputs."""
    if not entries:
        return {}
    started = time.monotonic()
    request_file = glide_root / 'new_reference_inputs.json'
    result_file = glide_root / 'new_reference_cores.json'
    result_file.unlink(missing_ok=True)
    groups = []
    refs = getattr(args, 'crem_docking_references', {})
    source = str(args.reference_poses.resolve())
    for job, _ in entries:
        group = dict(id=job['reference_id'], name=job['reference_name'],
                     output=str(job['reference_file'].resolve()))
        if refs:
            ref = refs[job['reference_id']]
            path = Path(ref['source'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
                raise RuntimeError('Frozen elite docking reference changed; submission withheld')
            source = str(path)
            group.update(smarts=ref['mcs_smarts'], core_atoms=ref['reference_atom_indices'])
        groups.append(group)
    if refs and len({refs[j['reference_id']]['source'] for j, _ in entries}) != 1:
        raise RuntimeError('Frozen elite docking references have inconsistent sources')
    request_file.write_text(json.dumps(dict(
        references=source, smarts=args.mcs_smarts,
        groups=groups,
        result=str(result_file.resolve()))))
    helper = Path(__file__).resolve().parents[1] / 'glide_inputs.py'
    c.run_command([args.schrodinger / 'run', helper, request_file.resolve()], cwd=glide_root)
    cores = json.loads(result_file.read_text())
    if set(cores) != {str(job['reference_id']) for job, _ in entries}:
        raise RuntimeError('Batched Glide references have incomplete group coverage')
    inputs = {}
    for job, ligand_file in entries:
        core = cores[str(job['reference_id'])]
        if not job['reference_file'].is_file() or (args.mcs_smarts and not core):
            raise RuntimeError('Batched Glide reference or core atoms missing')
        smarts = refs[job['reference_id']]['mcs_smarts'] if refs else args.mcs_smarts
        options = {}
        if getattr(args, 'glide_pose_point', None) is not None:
            from graft.glide_pose_selection import CONTACT_POSES_PER_LIG
            options['poses_per_lig'] = CONTACT_POSES_PER_LIG
        inputs[job['reference_id']] = c.write_constrained_glide_input(
            job['group_dir'], args.grid, ligand_file, job['reference_file'], smarts, core, **options)
    elapsed = time.monotonic() - started
    record_timing(glide_root, 'prepare_reference_inputs', elapsed)
    print(f'Glide timing: prepare {len(entries)} reference inputs={elapsed:.2f}s.', flush=True)
    return inputs


def collect_during_submissions(iteration, args, initial, incoming, done, wake,
                               extractor, merger, glide_root):
    """One collector owns all result publication while the main thread submits.

    Jobs enter the queue only after their durable JobId has been saved. The
    collector never submits work and hands remaining jobs back before tail polling.
    """
    pending = list(initial)
    terminal = 0
    while True:
        wake.clear()
        while True:
            try:
                pending.append(incoming.get_nowait())
            except Empty:
                break
        pending, finished = collect_finished_groups(
            iteration, args, pending, extractor, merger, glide_root)
        terminal += finished
        if done.is_set() and incoming.empty():
            return pending, terminal
        wake.wait(min(5, max(0.1, args.poll_interval)))


def prepare_glide_files(glide_root, ligprep_file, live=(), suite=None):
    """Remove results from another LigPrep input before inspecting job outputs.

    Keep matching-input files so asynchronous jobs survive worker restarts.
    Legacy outputs without a fingerprint are treated as stale.
    """
    digest = hashlib.sha256()
    with ligprep_file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    signature = digest.hexdigest()
    marker = glide_root / "ligprep_input.sha256"
    if marker.exists() and marker.read_text().strip() == signature:
        return

    if any(belongs_to_directory(job, group) for group in glide_root.glob('ref_*') for job in live):
        raise RuntimeError('Changed LigPrep input has live Glide jobs; files retained for reconciliation')
    removed = 0
    for group in glide_root.glob("ref_*"):
        if not group.is_dir():
            continue
        saved = load_record(group)
        if saved:
            if not saved.get('job_id'):
                raise RuntimeError('Changed input has an uncertain Glide launch; files retained')
            if suite is not None and any(j.get('status') not in TERMINAL for j in job_details(suite, saved['job_id'], group)):
                raise RuntimeError('Changed input has live saved Glide jobs; files retained')
            record_file(group).replace(group / f'glide_job.previous-{time.time_ns()}.json')
        artifacts = set(group.glob("glide*"))
        artifacts.update(group / name for name in (
            "best_poses.maegz", "docking_scores.tsv", "_reference.maegz",
            "ligprep_group.maegz",
        ))
        for path in artifacts:
            if path.is_file():
                path.unlink()
                removed += 1
    for name in ("best_poses.maegz", "best_poses.pending.maegz", "reference_map.tsv"):
        path = glide_root / name
        if path.is_file():
            path.unlink()
            removed += 1
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(signature + "\n")
    temporary.replace(marker)
    if removed:
        progress(f"Removed {removed} old Glide files for the current LigPrep input.")


def query_groups(iteration, db_path):
    groups = {}
    rows = database.glide_pending_rows(db_path, iteration)
    for cid, name, smiles, refid, sim in rows:
        groups.setdefault(refid, []).append((cid,name,smiles,sim))
    return groups


def mark_group(db_path, compounds, state, failed_stage=None, message=None):
    c.set_compound_state(
        [x[0] for x in compounds],
        state,
        failed_stage=failed_stage,
        failure_message=message,
        db_path=db_path,
    )


def failure_reason(directory, status):
    record = load_record(directory) or {}
    if record.get('timeout_requested_at'):
        return f"Glide job exceeded {record['timeout_seconds']}s; parent and children terminated"
    return f'Glide job ended with {status}'


def merge_current_best(args, glide_root, merger):
    files = sorted(glide_root.glob("ref_*/best_poses.maegz"))
    if files:
        pending_file = glide_root / "best_poses.pending.maegz"
        c.merge_best_pose_files(
            args.schrodinger, merger, files,
            pending_file, glide_root
        )
        pending_file.replace(glide_root / "best_poses.maegz")


def process_completed_job(iteration, args, job, extractor, merger, glide_root):
    if has_timeouts(load_record(job['group_dir'])):
        return process_timeout_job(iteration, args, job, merger, glide_root)
    started = time.monotonic()
    score_file, best_file = c.extract_scores_and_best_poses(
        args.schrodinger, extractor, job["output_file"], job["group_dir"]
    )
    extracted = time.monotonic()
    # Publish complete poses before making the group's scores eligible for GBSA.
    merge_current_best(args, glide_root, merger)
    published = time.monotonic()
    updated = c.update_scores(score_file, args.db_path)
    with c.open_sqlite(args.db_path) as conn:
        scored = {r[0] for r in conn.execute(
            "SELECT id FROM compound WHERE iteration=? AND docking_score IS NOT NULL",
            (iteration,),
        )}
    ids = [x[0] for x in job["compounds"]]
    failed = [cid for cid in ids if cid not in scored]
    if failed:
        c.set_compound_state(
            failed,
            "failed",
            failed_stage="glide",
            failure_message="No Glide pose",
            db_path=args.db_path,
        )
    print(f"Reference {job['reference_id']} completed: DB updated={updated}, no-pose={len(failed)}", flush=True)
    committed = time.monotonic()
    for phase, seconds in [('extract', extracted-started), ('publish', published-extracted),
                           ('db', committed-published)]:
        record_timing(job['group_dir'], phase, seconds)
    print(f"Glide timing: reference {job['reference_id']} extract={extracted-started:.2f}s, "
          f"publish={published-extracted:.2f}s, db={committed-published:.2f}s.", flush=True)
    logfile = job.get("log_file", job["group_dir"] / "glide.log")
    if logfile.exists():
        match = re.search(r"Total elapsed time for Glide script\s*=\s*([0-9.]+)\s*seconds",
                          logfile.read_text(errors="replace"))
        if match:
            detail(f"Glide reference {job['reference_id']} calculation completed; "
                  f"elapsed={format_elapsed(float(match.group(1)))}; current_time={current_time()}.", flush=True)



def process_timeout_job(iteration, args, job, merger, glide_root):
    directory = job['group_dir']
    record = load_record(directory)
    if not has_timeouts(record):
        raise RuntimeError('Glide timeout recovery has no durable native identity')
    summary = database.begin_glide_timeout_collection(
        args.db_path, iteration, job['reference_id'], record['job_id'])
    if summary is None:
        subjobs = record.get('subjob_timeouts')
        extra = {}
        if subjobs:
            native = job_details(args.schrodinger, record['job_id'], directory)
            if any(row.get('status') not in TERMINAL for row in native):
                raise RuntimeError('Glide subjob result collection waits for parent and children')
            parent = next(row for row in native if row['jobId'] == record['job_id'])
            if parent['status'] != 'DONE' or not record.get('downloaded'):
                raise RuntimeError('Glide parent must finish merging and download before subjob result collection')
            log_file, output_file = output_paths(directory)
            archive = directory / f'{log_file.stem}_subjobs.tar.gz'
            sources = [output_file] if output_file.exists() and output_file.stat().st_size else []
            # A child can finish between timeout intent and native stop. Native
            # DONE wins; retain that complete result rather than excluding it.
            successful = {row.get('jobName') for row in native
                          if row['jobId'] != record['job_id'] and row.get('status') == 'DONE'}
            excluded = {item['job_name'] for item in subjobs.values()} - successful
            extra = dict(exclude_subjobs=sorted(excluded),
                         subjob_archive=str(archive.resolve()),
                         ligprep_input=str((directory/'ligprep_group.maegz').resolve()))
        else:
            sources = timeout_pose_files(args.schrodinger, directory)
        folder = directory / 'timeout-results' / 'extracted'
        folder.mkdir(parents=True, exist_ok=True)
        ids = [row[0] for row in job['compounds']]
        scores = {}
        report = dict(scored=0, read_errors=[], inputs=[])
        if sources or extra:
            request = dict(inputs=[str(p.resolve()) for p in sources], ids=ids, output=str(folder.resolve()), **extra)
            request['attraction_point'] = pose_selection_point(args, iteration)
            request_file = folder / 'request.json'
            request_file.write_text(json.dumps(request))
            helper = Path(__file__).resolve().parents[1] / 'glide_timeout_results.py'
            c.run_command([args.schrodinger/'run', helper, request_file], cwd=directory)
            with (folder / 'docking_scores.tsv').open(newline='') as stream:
                scores = {int(row['id']): float(row['docking_score'])
                          for row in csv.DictReader(stream, delimiter='\t')}
            report = json.loads((folder / 'report.json').read_text())
        database.validate_glide_timeout_scores(args.db_path, iteration, job['reference_id'], scores)
        if scores:
            # Publish the matching poses before making recovered scores eligible.
            for name in ('best_poses.maegz', 'docking_scores.tsv'):
                pending = directory / ('timeout-' + name)
                shutil.copy2(folder / name, pending)
                pending.replace(directory / name)
            merge_current_best(args, glide_root, merger)
        record = load_record(directory)
        summary = dict(job_id=record['job_id'], timeout_seconds=record['timeout_seconds'],
                       scored_ids=sorted(scores), sources=record.get('timeout_sources', []), report=report)
        if subjobs:
            summary['subjob_timeouts'] = subjobs
        database.store_glide_timeout_scores(args.db_path, iteration, job['reference_id'],
            record['job_id'], scores, record['timeout_seconds'], summary,
            timeout_ids=report.get('timeout_ids'))
    record = load_record(directory)
    record['timeout_results_imported'] = True
    record['timeout_result_count'] = len(summary['scored_ids'])
    save_record(directory, record)
    print(f"Reference {job['reference_id']}: timeout results collected; "
          f"finite scores={len(summary['scored_ids'])}; no docking resubmission.", flush=True)


def recover_timeout_groups(args, iteration, glide_root):
    records = [(path.parent, load_record(path.parent))
               for path in sorted(glide_root.glob('ref_*/glide_job.json'))]
    pending = [(directory, record) for directory, record in records
               if has_timeouts(record) and not record.get('timeout_results_imported')]
    if not pending:
        return
    # Legacy workers may already have marked timeout members failed. Enroll
    # every pending native tree before slow I/O, so independent GBSA workers
    # cannot rank an iteration whose timeout results still need collection.
    for directory, record in pending:
        database.begin_glide_timeout_collection(args.db_path, iteration,
            int(directory.name.removeprefix('ref_')), record['job_id'])
    prepared = stage_directory(args.output, iteration, 'ligprep') / 'ligprep.maegz'
    signature = glide_root / 'ligprep_input.sha256'
    digest = hashlib.sha256()
    with prepared.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    if not signature.exists() or signature.read_text().strip() != digest.hexdigest():
        raise RuntimeError('Cannot recover Glide timeout results for a changed LigPrep input')
    merger = c.create_best_pose_merger(glide_root)
    for directory, record in pending:
        refid = int(directory.name.removeprefix('ref_'))
        log_file, output_file = output_paths(directory)
        status = group_status(args.schrodinger, directory, log_file, output_file,
                              timeout_seconds=getattr(args, 'glide_job_timeout', 1800))
        if status not in TERMINAL:
            continue
        rows = database.glide_timeout_rows(args.db_path, iteration, refid)
        job = dict(group_dir=directory, reference_id=refid, compounds=rows)
        process_timeout_job(iteration, args, job, merger, glide_root)


def collect_finished_groups(iteration, args, jobs, extractor, merger, glide_root, *, skip_active=False):
    """Publish each verified finished group without waiting for its peers."""
    if not jobs:
        return [], 0
    started = time.monotonic()
    live = active_jobs(args.schrodinger)
    elapsed = time.monotonic()-started
    record_timing(glide_root, 'collection_active_job_query', elapsed)
    detail(f'Glide timing: collection active-job query={elapsed:.2f}s.', flush=True)
    pending = []
    terminal = 0
    for job in jobs:
        job['log_file'], job['output_file'] = output_paths(job['group_dir'])
        # Between submissions, skip known active trees cheaply. The normal
        # polling pass still reconciles their runtime limits and live children.
        if skip_active and any(belongs_to_directory(native, job['group_dir']) for native in live):
            pending.append(job)
            continue
        started = time.monotonic()
        status = group_status(args.schrodinger, job['group_dir'], job['log_file'], job['output_file'], live,
                              timeout_seconds=getattr(args, 'glide_job_timeout', 1800))
        # Recovery may have just discovered a native name or downloaded its log.
        job['log_file'], job['output_file'] = output_paths(job['group_dir'])
        elapsed = time.monotonic()-started
        record_timing(job['group_dir'], 'status_download', elapsed)
        detail(f"Glide timing: reference {job['reference_id']} status/download="
               f'{elapsed:.2f}s.', flush=True)
        if status == 'DONE':
            if has_timeouts(load_record(job['group_dir'])):
                process_timeout_job(iteration, args, job, merger, glide_root)
            elif job['output_file'].exists() and job['output_file'].stat().st_size:
                process_completed_job(iteration, args, job, extractor, merger, glide_root)
            else:
                mark_group(args.db_path, job['compounds'], State.FAILED, failed_stage='glide',
                           message='Completed Glide job produced no poses')
        elif status is not None and status not in ('RUNNING', 'UNKNOWN'):
            if has_timeouts(load_record(job['group_dir'])):
                process_timeout_job(iteration, args, job, merger, glide_root)
            else:
                mark_group(args.db_path, job['compounds'], State.FAILED, failed_stage='glide',
                           message=failure_reason(job['group_dir'], status))
        else:
            pending.append(job)
            continue
        terminal += 1
    return pending, terminal


@timed_stage("Glide stage worker")
def _run_stage(args, ns):
    args.poll_interval = ns.poll_interval
    args.completion_fraction = ns.completion_fraction
    if ns.tail_timeout is not None:
        args.tail_timeout = ns.tail_timeout

    iteration = ns.iteration or c.find_iteration_for_state("glide", args.db_path)
    if iteration is None:
        print("No iteration requires Glide.")
        return

    reason = database.upstream_completion_reason(
        args.db_path, iteration, getattr(args, "target_count", 1), "glide",
    )
    if reason:
        print(f"Iteration {iteration}: Glide waiting: {reason}.")
        return

    from graft.chemistry.scaffold import prepare_iteration_scaffold
    prepare_iteration_scaffold(args, iteration, include_generation=False)
    args.glide_pose_point = pose_selection_point(args, iteration)
    args.crem_docking_references = database.crem_glide_references(args.db_path, iteration)

    # Assign nearest reference for any still-undocked compounds.
    c.assign_reference_compounds(iteration, args)
    recover_timeout_groups(args, iteration, stage_directory(args.output, iteration, 'glide'))
    groups = query_groups(iteration, args.db_path)
    fresh_references = database.fresh_glide_references(args.db_path, iteration)

    claimed_groups = {}
    for reference_id, compounds in groups.items():
        claimed_ids = set(
            database.claim_compounds(
                args.db_path,
                [compound[0] for compound in compounds],
                expected_state=("ligprepped", "glide_running"),
                claimed_state="glide_running",
            )
        )
        if claimed_ids:
            claimed_groups[reference_id] = [
                compound for compound in compounds if compound[0] in claimed_ids
            ]
    groups = claimed_groups
    if not groups:
        print(f"Iteration {iteration}: no Glide-pending compounds.")
        return

    work_started(f"Iteration {iteration}: processing Glide for {len(groups)} reference groups.")
    if args.glide_pose_point is not None:
        print(f'Glide pose selection: contact within 2 A of {args.glide_pose_point}, then lowest score; '
              'no-contact fallback=lowest score; new inputs retain up to 10 poses/state.', flush=True)
    ligprep_file = stage_directory(args.output, iteration, 'ligprep') / "ligprep.maegz"
    if not ligprep_file.exists():
        raise FileNotFoundError(f"LigPrep output not found: {ligprep_file}")

    glide_root = stage_directory(args.output, iteration, 'glide')
    glide_root.mkdir(parents=True, exist_ok=True)
    live = active_jobs(args.schrodinger)
    prepare_glide_files(glide_root, ligprep_file, live, args.schrodinger)
    group_ligand_files = c.split_ligprep_by_reference(
        args.schrodinger, ligprep_file, groups, glide_root
    )
    if not group_ligand_files:
        raise RuntimeError('LigPrep output contains no current compound IDs; '
                           'rerun LigPrep for this iteration. Glide claims remain retryable.')
    score_extractor = c.create_score_extractor(glide_root, attraction_point=args.glide_pose_point)
    merger = c.create_best_pose_merger(glide_root)

    jobs = []
    unsubmitted = []
    reserved = []
    for refid, compounds in groups.items():
        ligand_file = group_ligand_files.get(refid)
        if ligand_file is None:
            mark_group(args.db_path, compounds, "failed", failed_stage="glide", message="No LigPrep structures for reference group")
            continue
        # Frozen parent-pose exports use CMPID titles. The legacy name lookup
        # deliberately accepts only iteration-0 reference ligands.
        refname = (f'CMPID_{refid}' if args.crem_docking_references
                   else c.get_reference_name(refid, args.db_path))
        gdir = glide_root / f"ref_{refid}"
        gdir.mkdir(parents=True, exist_ok=True)
        ref_file = gdir / "_reference.maegz"
        log_file, output_file = output_paths(gdir)

        job = dict(reference_id=refid, reference_name=refname,
                   group_dir=gdir, reference_file=ref_file,
                   output_file=output_file, log_file=log_file,
                   input_count=len(compounds), compounds=compounds)

        status = group_status(args.schrodinger, gdir, log_file, output_file, live,
                              timeout_seconds=getattr(args, 'glide_job_timeout', 1800))
        log_file, output_file = output_paths(gdir)
        job.update(log_file=log_file, output_file=output_file)
        if refid in fresh_references and status in TERMINAL:
            discard_terminal_results(gdir)
            status = None
        if status in ('RUNNING', 'UNKNOWN'):
            mark_group(args.db_path, compounds, State.GLIDE_RUNNING)
            jobs.append(job)
            record = load_record(gdir) or {}
            command = record.get('command', [])
            reserved.append(command[command.index('-HOST') + 1]
                            if '-HOST' in command and command.index('-HOST') + 1 < len(command)
                            else None)
            continue
        if status == 'DONE':
            if has_timeouts(load_record(gdir)):
                process_timeout_job(iteration, args, job, merger, glide_root)
            elif output_file.exists() and output_file.stat().st_size:
                process_completed_job(iteration, args, job, score_extractor, merger, glide_root)
            else:
                mark_group(args.db_path, compounds, State.FAILED, failed_stage='glide',
                           message='Completed Glide job produced no poses')
            continue
        if status is not None:
            if (load_record(gdir) or {}).get('timeout_requested_at'):
                process_timeout_job(iteration, args, job, merger, glide_root)
            else:
                mark_group(args.db_path, compounds, State.FAILED, failed_stage='glide',
                           message=failure_reason(gdir, status))
            continue

        unsubmitted.append((job, ligand_file))

    counts_file = glide_root / 'ligprep_counts.json'
    structure_counts = json.loads(counts_file.read_text()) if counts_file.exists() else {}
    counts = {job['reference_id']: structure_counts.get(str(job['reference_id']), job['input_count'])
              for job, _ in unsubmitted}
    resources = glide_group_resources(getattr(args, 'glide_host', getattr(args, 'host', '')),
                                      getattr(args, 'glide_cpus', None), counts, reserved,
                                      priority_order=database.uses_druglike_priority(args.db_path, iteration))
    deferred = len(unsubmitted) - len(resources)
    if deferred:
        print(f'Glide: {deferred} reference groups waiting for the shared core budget.', flush=True)

    selected = [(job, ligand) for job, ligand in unsubmitted if job['reference_id'] in resources]
    molecules_per_job = iteration_batch_size(args.db_path, iteration) if selected else None
    incoming, done, wake = Queue(), Event(), Event()
    # Each thread opens its own SQLite connections. Only the collector writes
    # result files/merged poses; the submitter owns new launch records until queued.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='glide-collector') as pool:
        collector = pool.submit(copy_context().run, collect_during_submissions,
                                iteration, args, jobs, incoming, done, wake,
                                score_extractor, merger, glide_root)
        try:
            inputs = prepare_new_inputs(args, selected, glide_root)
            submission_verified = False
            for job, ligand_file in selected:
                if collector.done():
                    collector.result()  # Stop new launches after a collection failure.
                refid = job['reference_id']
                host = resources[refid]
                options = glide_job_options(host, counts[refid], molecules_per_job=molecules_per_job)
                njobs = options[options.index('-NJOBS') + 1] if '-NJOBS' in options else 'host default'
                print(f"Glide reference {refid}: {counts[refid]} prepared ligands; "
                      f'NJOBS={njobs}, HOST={host}; target ~{molecules_per_job} prepared structures/subjob.', flush=True)
                started = time.monotonic()
                try:
                    if getattr(args, 'use_slurm', False) and not submission_verified:
                        from graft.schrodinger_guard import verify_submission_host
                        verify_submission_host(args.schrodinger, host)
                        submission_verified = True
                    submitted = c.submit_glide(
                        args.schrodinger, inputs[refid], job['group_dir'], host,
                        ligand_count=counts[refid], molecules_per_job=molecules_per_job)
                except SubmissionBlocked as exc:
                    print(f'Iteration {iteration}: Glide submissions waiting: {exc}.')
                    return
                job.update(submitted)
                mark_group(args.db_path, job['compounds'], "glide_running")
                incoming.put(job)
                wake.set()
                elapsed = time.monotonic()-started
                record_timing(job['group_dir'], 'submission', elapsed)
                print(f'Glide timing: reference {refid} submission={elapsed:.2f}s.', flush=True)
        finally:
            done.set()
            wake.set()
            jobs, collected_during_submission = collector.result()

    if not jobs:
        print("No running Glide jobs remain.")
        return

    total = len(jobs) + collected_during_submission
    terminal = collected_during_submission
    tail_started = None
    start = time.monotonic()
    pending = list(jobs)

    while pending:
        pending, finished = collect_finished_groups(iteration, args, pending, score_extractor, merger, glide_root)
        terminal += finished
        if not pending:
            break

        frac = terminal / total if total else 1.0
        now = time.monotonic()
        if tail_started is None and frac >= args.completion_fraction:
            tail_started = now
            progress(f"Tail phase started: {terminal}/{total} terminal, timeout={args.tail_timeout}s")
        if tail_started is not None and now - tail_started >= args.tail_timeout:
            print(f"Tail timeout reached; leaving {len(pending)} jobs running for a later 03-glide.py invocation.")
            break
        progress(f"Glide: {len(pending)} running, {terminal}/{total} terminal")
        time.sleep(args.poll_interval)

    merge_current_best(args, glide_root, merger)


def main(argv=None):
    p = argparse.ArgumentParser(description="Independent asynchronous Glide stage")
    p.add_argument("project_toml", type=Path)
    p.add_argument("--iteration", type=int)
    p.add_argument("--poll-interval", type=int, default=30)
    p.add_argument("--completion-fraction", type=float, default=0.95)
    p.add_argument("--tail-timeout", type=int)
    p.add_argument("--schrodinger", type=Path)
    ns = p.parse_args(argv)
    args = c.configure_project(ns.project_toml, ns.schrodinger)
    with database.stage_lock(args.db_path, "glide"):
        _run_stage(args, ns)


if __name__ == "__main__":
    main()
