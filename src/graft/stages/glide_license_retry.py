"""SQLite-owned vacancy backfill for native Glide license failures only."""
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from graft import _core as c, database
from graft.config import (GLIDE_SLURM_MEMORY_MB, GLIDE_LICENSE_RETRY_CPUS,
                          glide_group_resources, schrodinger_job_options)
from graft.glide_license_inputs import license_exhausted, license_failure_inputs
from graft.glide_recovery import load_record, save_record, output_paths, group_status, has_timeouts
from graft.schrodinger_guard import job_details, active_jobs, TERMINAL, SubmissionBlocked
from graft.states import GlideRetryState
from graft.stages._logging import work_started


def _helper(args, folder, request):
    path = folder/'license_request.json'
    result = folder/'license_result.json'
    result.unlink(missing_ok=True)
    path.write_text(json.dumps(dict(request, result=str(result.resolve()))))
    helper = Path(__file__).resolve().parents[1]/'glide_license_inputs.py'
    c.run_command([args.schrodinger/'run', helper, path.resolve()], cwd=folder)
    return json.loads(result.read_text())


def inspect_group(args, iteration, directory):
    """Only inspect verified terminal, downloaded originals; never touch live work."""
    directory = Path(directory)
    record = load_record(directory)
    if not record or not record.get('downloaded') or record.get('license_failures_inspected'):
        return
    log, _ = output_paths(directory)
    if not log.exists():
        return  # Legacy/test records without native logs are not failure evidence.
    text = log.read_text(errors='replace')
    if not license_exhausted(text):
        record['license_failures_inspected'] = True
        save_record(directory, record)
        return
    ref = int(directory.name.removeprefix('ref_'))
    database.begin_glide_license_inspection(args.db_path, iteration, ref, record['job_id'])
    native = job_details(args.schrodinger, record['job_id'], directory)
    if any(j.get('status') not in TERMINAL for j in native):
        raise RuntimeError('Glide license failure inspection waits for the entire native tree')
    serial = len(native) == 1 and native[0]['status'] == 'FAILED'
    if not serial and not re.search(r"ERROR: Subjob '[^']+' has failed after \d+ retries\.", text):
        database.enqueue_glide_license_retries(args.db_path, iteration, ref, record['job_id'], {})
        record['license_failures_inspected'] = True
        save_record(directory, record)
        return
    ids = [r[0] for r in database.glide_timeout_rows(args.db_path, iteration, ref)]
    tasks = _helper(args, directory, dict(action='plan', log=str(log.resolve()),
        archive=str(log.with_name(log.stem+'_subjobs.tar.gz').resolve()),
        input=str(log.with_suffix('.in').resolve()), ligands=str((directory/'ligprep_group.maegz').resolve()),
        ids=ids, serial_license_failure=serial))
    # A later successful native execution wins over an old exhausted-attempt log.
    successful = {j.get('jobName') for j in native if j.get('status') == 'DONE' and j['jobId'] != record['job_id']}
    tasks = {name: payload for name, payload in tasks.items() if name not in successful}
    database.enqueue_glide_license_retries(args.db_path, iteration, ref, record['job_id'], tasks)
    record['license_failures_inspected'] = True
    save_record(directory, record)
    if tasks:
        print(f'Glide reference {ref}: queued {len(tasks)} license-failed subjob(s) for available Glide slots.', flush=True)


def inspect_downloaded(args, iteration, root):
    normal_pending = bool(database.glide_pending_rows(args.db_path, iteration))
    for path in sorted(root.glob('ref_*/glide_job.json')):
        record = load_record(path.parent)
        if not normal_pending and record and record.get('job_id') and not record.get('downloaded'):
            jobs = job_details(args.schrodinger, record['job_id'], path.parent)
            if all(j.get('status') in TERMINAL for j in jobs):
                subprocess.run([str(args.schrodinger/'jsc'), 'download', '--cwd', record['job_id']],
                               cwd=path.parent, check=True, capture_output=True, text=True, timeout=60)
                record['downloaded'] = True
                save_record(path.parent, record)
        inspect_group(args, iteration, path.parent)


def _originals_finished(args, iteration, root):
    if database.glide_license_inspection_pending(args.db_path, iteration):
        return False
    if database.glide_pending_rows(args.db_path, iteration):
        return False
    for path in sorted(root.glob('ref_*/glide_job.json')):
        record = load_record(path.parent)
        if not record.get('job_id') or not record.get('downloaded'):
            return False
        if has_timeouts(record) and not record.get('timeout_results_imported'):
            return False
        if any(j.get('status') not in TERMINAL for j in job_details(args.schrodinger, record['job_id'], path.parent)):
            return False
    return True


def _available_retry_host(args, iteration, root):
    """Reserve whole live native HOST requests, not just currently busy children.

    Original groups may still launch their unsubmitted children. Unknown native
    identities/status and unsubmitted original groups cannot release their budget.
    Bare hosts have no measurable budget and retain the terminal-only fallback.
    """
    if database.glide_license_inspection_pending(args.db_path, iteration):
        return False
    host = getattr(args, 'glide_host', args.host)
    cpus = getattr(args, 'glide_cpus', None)
    explicit = schrodinger_job_options(host, cpus)[1]
    if any(not re.fullmatch(r'[^:]+:[1-9][0-9]*', entry) for entry in explicit.split()):
        if _originals_finished(args, iteration, root):
            return host.split()[0].split(':')[0]+f':{GLIDE_LICENSE_RETRY_CPUS}'
        return None
    records = {int(p.parent.name.removeprefix('ref_')): (p.parent, load_record(p.parent))
               for p in root.glob('ref_*/glide_job.json')}
    if any(row[3] not in records for row in database.glide_pending_rows(args.db_path, iteration)):
        return False  # New original groups have priority over recovery backfill.
    reserved = []
    for directory, record in records.values():
        if not record or not record.get('job_id'):
            return False
        if record.get('downloaded') and (not has_timeouts(record) or record.get('timeout_results_imported')):
            continue  # Downloaded original trees were verified terminal.
        jobs = job_details(args.schrodinger, record['job_id'], directory)
        if all(job.get('status') in TERMINAL for job in jobs):
            continue  # Pending result import is a barrier, not a CPU reservation.
        command = record.get('command', [])
        request = command[command.index('-HOST')+1] if '-HOST' in command and command.index('-HOST')+1 < len(command) else None
        reserved.append(request)
    return glide_group_resources(host, cpus, {0: GLIDE_LICENSE_RETRY_CPUS}, reserved).get(0)


def _collect(args, iteration, root, task, folder, output, merge_current_best, merger, *, pose_inputs=None):
    for path, expected in task['payload']['files'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Frozen Glide license retry source changed during calculation')
    group = root/f"ref_{task['reference_id']}"
    sources = [output] if pose_inputs is None else pose_inputs
    sources = [p for p in sources if p.exists() and p.stat().st_size]
    for source in sources:
        _helper(args, folder, dict(action='validate', ligands=str((folder/'ligands.maegz').resolve()),
                                  poses=str(source.resolve()), ids=task['payload']['ids']))
    ids = [r[0] for r in database.glide_timeout_rows(args.db_path, iteration, task['reference_id'])]
    # One selector sees both the old best and every newly computed state. Native
    # contact/score/tie and exact fixed-frame core validation remain unchanged.
    old = group/'best_poses.maegz'
    inputs = [str(p.resolve()) for p in [old, *sources] if p.exists() and p.stat().st_size]
    collect = folder/'collected'
    collect.mkdir(exist_ok=True)
    request = dict(inputs=inputs, ids=ids, output=str(collect.resolve()),
                   core_input=task['payload']['input'], attraction_point=getattr(args, 'glide_pose_point', None))
    path = folder/'collect_request.json'
    path.write_text(json.dumps(request))
    helper = Path(__file__).resolve().parents[1]/'glide_timeout_results.py'
    c.run_command([args.schrodinger/'run', helper, path.resolve()], cwd=folder)
    report = json.loads((collect/'report.json').read_text())
    if report['read_errors']:
        raise RuntimeError('Glide license retry result read failed; original results retained')
    with (collect/'docking_scores.tsv').open() as stream:
        scores = {int(r['id']): float(r['docking_score']) for r in csv.DictReader(stream, delimiter='\t')}
    database.validate_glide_license_retry_scores(args.db_path, task['id'], scores)
    # Keep previous group artifacts as audit, and publish matching poses before
    # SQLite makes any new scores eligible. Repeating after a crash is idempotent.
    if scores:
        for name in ('best_poses.maegz', 'docking_scores.tsv'):
            target = group/name
            backup = folder/('previous_'+name)
            if target.exists() and not backup.exists():
                shutil.copy2(target, backup)
            pending = group/('license_'+name)
            shutil.copy2(collect/name, pending)
            pending.replace(target)
        merge_current_best(args, root, merger)
    database.store_glide_license_retry_scores(args.db_path, task['id'], scores)


def run_tail(args, iteration, root, merge_current_best, merger):
    """Recover one retry or submit it into vacant budget while originals run."""
    if getattr(args, 'glide_shared_queue', False):
        from graft.stages.glide_shared import service
        return service(args, iteration, root, merger, merge_current_best)
    tasks = database.glide_license_retries(args.db_path, iteration)
    if not tasks:
        return
    running = [t for t in tasks if t['state'] == GlideRetryState.RUNNING]
    host = None
    if len(running) > 1:
        raise RuntimeError('More than one Glide license retry retry owns native work')
    if running:
        task = running[0]
    else:
        host = _available_retry_host(args, iteration, root)
        if not host:
            return
        task_id = database.claim_glide_license_retry(args.db_path, iteration, time.time())
        if task_id is None:
            print('Glide license retries: waiting for retry cooldown.', flush=True)
            return
        task = next(t for t in database.glide_license_retries(args.db_path, iteration) if t['id'] == task_id)
    folder = root/'license-retries'/f"task_{task['id']}"/f"attempt_{task['attempts']}"
    folder.mkdir(parents=True, exist_ok=True)
    record = load_record(folder)
    if record is None:
        host = host or _available_retry_host(args, iteration, root)
        if not host:
            return
        work_started(f"Glide license retry: preparing task {task['id']}, attempt {task['attempts']}.")
        prepared = _helper(args, folder, dict(action='prepare', payload=task['payload'], output=str(folder.resolve())))
        # Keep serial recovery admission while ordinary groups run in parallel.
        # Each attempt retains its own saved native identity and strict inputs.
        try:
            c.submit_glide(args.schrodinger, Path(prepared['input']), folder, host,
                           memory_mb=GLIDE_SLURM_MEMORY_MB if getattr(args, 'use_slurm', False) else None)
        except SubmissionBlocked as exc:
            print(f'Glide license retry submission waiting: {exc}', flush=True)
            return
        print(f"Glide license retry: task {task['id']}, attempt {task['attempts']}; "
              f"{prepared['structures']} prepared states, HOST={host}.", flush=True)
        return
    log, output = output_paths(folder)
    status = group_status(args.schrodinger, folder, log, output, active_jobs(args.schrodinger),
                          timeout_seconds=getattr(args, 'glide_job_timeout', 1800))
    if status not in TERMINAL:
        print(f"Glide license retry: recovering task {task['id']} ({status}).", flush=True)
        return
    record = load_record(folder)
    if not record.get('downloaded'):
        subprocess.run([str(args.schrodinger/'jsc'), 'download', '--cwd', record['job_id']], cwd=folder,
                       check=True, capture_output=True, text=True, timeout=60)
        record['downloaded'] = True
        save_record(folder, record)
    if not log.exists():
        raise RuntimeError('Missing terminal Glide license retry log; task retained')
    # NJOBS=1: a native exhausted license failure must never turn into a
    # permanent scientific no-pose failure, even when its driver ends DONE.
    text = log.read_text(errors='replace')
    if re.search(r"ERROR: Subjob '[^']+' has failed after \d+ retries\.", text):
        failed_license = bool(license_failure_inputs(text, log.with_name(log.stem+'_subjobs.tar.gz')))
    else:
        failed_license = license_exhausted(text) and (status != 'DONE' or
            not output.exists() or not output.stat().st_size)
    if failed_license:
        database.finish_glide_license_retry(args.db_path, task['id'], license_error=True, now=time.time())
        print(f"Glide license retry: task {task['id']} still lacks licenses; retained for a later pass.", flush=True)
    elif status == 'DONE' and not has_timeouts(record):
        work_started(f"Glide license retry: collecting task {task['id']}, attempt {task['attempts']}.")
        _collect(args, iteration, root, task, folder, output, merge_current_best, merger)
    else:
        database.finish_glide_license_retry(args.db_path, task['id'])
