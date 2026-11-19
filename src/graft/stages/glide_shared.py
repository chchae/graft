"""One SQLite work pool across references; native JobControl owns calculations.

No pickle restart can reset live native jobs. The single collector publishes
poses while the submitter fills a bounded, cross-project-fenced admission wave.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime
from pathlib import Path
from threading import Event
import subprocess
import time

from graft import _core as c, database
from graft.config import (GLIDE_SHARED_CPU_RESERVATION, GLIDE_SHARED_TARGET_SECONDS,
                          GLIDE_SLURM_MEMORY_MB, GLIDE_LICENSE_RETRY_DELAY_SECONDS,
                          schrodinger_job_options)
from graft.glide_capacity import license_capacity
from graft.glide_license_inputs import license_exhausted, license_failure_inputs
from graft.glide_recovery import (load_record, save_record, output_paths, group_status,
                                  submission_batch, has_timeouts, timeout_pose_files)
from graft.schrodinger_guard import TERMINAL, SubmissionBlocked, job_details
from graft.stages import glide_license_retry as retry
from graft.stages._logging import work_started


def enroll(args, iteration, entries, inputs, default_batch):
    target = database.glide_observed_batch_size(args.db_path, iteration, default_batch, GLIDE_SHARED_TARGET_SECONDS)
    print(f'Glide shared queue: target {target} prepared states/batch; complete compound state sets stay together.', flush=True)
    for job, ligands in entries:
        folder = job['group_dir']
        if load_record(folder):
            raise RuntimeError('Cannot convert an existing native Glide parent to shared tasks')
        result = retry._helper(args, folder, dict(action='ordinary_plan', input=str(inputs[job['reference_id']].resolve()),
             ligands=str(ligands.resolve()), ids=[r[0] for r in job['compounds']], batch_size=target))
        database.freeze_glide_shared_plan(args.db_path, iteration, job['reference_id'], result['source'],
                                          result['tasks'], result['missing'])


def attempt_directory(root, task):
    # Retain paths of previously enrolled license retries, including uncertain
    # intents saved before the shared scheduler was installed.
    return root/'license-retries'/f"task_{task['id']}"/f"attempt_{task['attempts']}"


def _runtime(args, iteration, task, folder):
    record = load_record(folder)
    rows = job_details(args.schrodinger, record['job_id'], folder)
    parent = next(r for r in rows if r['jobId'] == record['job_id'])
    try:
        start = datetime.fromisoformat(parent['timeStarted'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(parent['statusUpdated'].replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError):
        return
    database.record_glide_batch_runtime(args.db_path, task, iteration, (end-start).total_seconds())


def reconcile(args, iteration, root, merger, merge_best):
    """Only the collector calls this; never infer terminal state from output."""
    changed = database.glide_shared_publication(args.db_path, iteration)
    for task in database.glide_license_retries(args.db_path, iteration, state='running'):
        folder = attempt_directory(root, task)
        record = load_record(folder)
        if not record:
            continue  # Claimed before a crash, but no native intent was made.
        log, output = output_paths(folder)
        status = group_status(args.schrodinger, folder, log, output, [],
                              timeout_seconds=getattr(args, 'glide_job_timeout', 1800))
        if status not in TERMINAL:
            continue
        record = load_record(folder)
        if not record.get('downloaded'):
            subprocess.run([str(args.schrodinger/'jsc'), 'download', '--cwd', record['job_id']], cwd=folder,
                           check=True, capture_output=True, text=True, timeout=60)
            record['downloaded'] = True
            save_record(folder, record)
        if not log.exists():
            raise RuntimeError('Missing terminal shared Glide log; native identity retained')
        text = log.read_text(errors='replace')
        if "has failed after" in text:
            failed_license = bool(license_failure_inputs(text, log.with_name(log.stem+'_subjobs.tar.gz')))
        else:
            failed_license = license_exhausted(text) and (status != 'DONE' or not output.exists() or not output.stat().st_size)
        if failed_license:
            database.finish_glide_license_retry(args.db_path, task['id'], license_error=True, now=time.time())
            print(f'Glide shared pool: task {task["id"]} license checkout exhausted; '
                  f'retry eligible in {GLIDE_LICENSE_RETRY_DELAY_SECONDS}s when CPU/license slots are available.', flush=True)
        elif status == 'DONE' or has_timeouts(record):
            # Group files are published immediately. A separate SQLite barrier
            # fences GBSA until the root merged library has caught up as well.
            database.glide_shared_publication(args.db_path, iteration, True)
            sources = timeout_pose_files(args.schrodinger, folder) if has_timeouts(record) else None
            retry._collect(args, iteration, root, task, folder, output, lambda *a: None, merger,
                           pose_inputs=sources)
            changed = True
            if has_timeouts(record):
                record = load_record(folder)
                record['timeout_results_imported'] = True
                save_record(folder, record)
            else:
                _runtime(args, iteration, task, folder)
        else:
            database.finish_glide_license_retry(args.db_path, task['id'])
    if changed:
        merge_best(args, root, merger)
        database.glide_shared_publication(args.db_path, iteration, False)


def _cpu_capacity(args, root):
    """Reserve legacy parents until their full native trees are terminal."""
    host = getattr(args, 'glide_host', args.host)
    explicit = schrodinger_job_options(host, getattr(args, 'glide_cpus', None))[1]
    hosts = {}
    for entry in explicit.split():
        name, colon, n = entry.rpartition(':')
        if not colon or not n.isdigit() or int(n) < 1:
            raise RuntimeError('Shared Glide requires an explicit CPU budget')
        hosts[name] = hosts.get(name, 0)+int(n)
    for path in root.glob('ref_*/glide_job.json'):
        record = load_record(path.parent)
        if not record or not record.get('job_id'):
            return {}
        if record.get('downloaded') and (not has_timeouts(record) or record.get('timeout_results_imported')):
            continue
        rows = job_details(args.schrodinger, record['job_id'], path.parent)
        if all(row['status'] in TERMINAL for row in rows):
            continue
        command = record.get('command', [])
        if '-HOST' not in command:
            return {}
        for entry in command[command.index('-HOST')+1].split():
            name, colon, n = entry.rpartition(':')
            if not colon or not n.isdigit() or name not in hosts:
                return {}
            hosts[name] -= int(n)
    return hosts if all(n >= 0 for n in hosts.values()) else {}


def service_pass(args, iteration, root, merger, merge_best, legacy_jobs=(), extractor=None, collect_legacy=None, capacity_hint=None):
    """Recover all active tasks and fill vacancies from all references fairly."""
    done = Event()
    def collect():
        pending = list(legacy_jobs)
        while True:
            if pending and collect_legacy:
                pending, _ = collect_legacy(iteration, args, pending, extractor, merger, root, native_snapshot=[])
            reconcile(args, iteration, root, merger, merge_best)
            if done.is_set():
                return pending
            done.wait(5)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='glide-shared-collector') as pool:
        future = pool.submit(copy_context().run, collect)
        try:
            counts = database.glide_queue_counts(args.db_path, iteration, time.time())
            if not counts['pending'] and not counts['running']:
                return
            if (capacity_hint and capacity_hint[0] > 0 and
                    time.monotonic()-capacity_hint[1] < 30 and
                    counts['running'] >= capacity_hint[0]):
                return  # Hint withholds launches only; it never authorizes one.
            if not counts['ready'] and not counts['running']:
                return
            if database.glide_license_inspection_pending(args.db_path, iteration):
                return
            with submission_batch(args.schrodinger) as (_, launch):
                try:
                    license = license_capacity(args.schrodinger)
                except (OSError, subprocess.SubprocessError, ValueError) as exc:
                    print(f'Glide shared admission waiting: cannot verify licenses: {exc}', flush=True)
                    return
                hosts = _cpu_capacity(args, root)
                capacity = min(sum(hosts.values())//GLIDE_SHARED_CPU_RESERVATION, license['total'])
                if capacity_hint is not None:
                    capacity_hint[:] = [capacity, time.monotonic()]
                running = database.glide_license_retries(args.db_path, iteration, state='running')
                for task in running:
                    folder = attempt_directory(root, task)
                    record = load_record(folder)
                    if record and not record.get('job_id'):
                        print('Glide shared admission waiting for an uncertain native launch.', flush=True)
                        return
                    if record:
                        command = record['command']
                        host = command[command.index('-HOST')+1].split(':')[0]
                        if host not in hosts:
                            return
                        hosts[host] -= GLIDE_SHARED_CPU_RESERVATION
                # Existing claims without a native intent retain their attempt
                # number and must launch before any new claims.
                unlaunched = [t for t in running if load_record(attempt_directory(root, t)) is None]
                free = min(max(0, capacity-len(running))+len(unlaunched), license['available'])
                print(f'Glide shared pool: active={len(running)}, capacity={capacity}, '
                      f'license_free={license["available"]}, new_admissions<={free}.', flush=True)
                if license['available'] == 0:
                    print('Glide shared pool: waiting for free licenses; '
                          f'recheck in {getattr(args, "poll_interval", 30)}s.', flush=True)
                for _ in range(free):
                    if future.done():
                        future.result()
                    if unlaunched:
                        task = unlaunched.pop(0)
                    else:
                        task_id = database.claim_glide_license_retry(args.db_path, iteration, time.time(), max_running=capacity)
                        if task_id is None:
                            break
                        task = database.glide_license_retries(args.db_path, iteration, task_id=task_id)[0]
                    host = next((h for h, n in hosts.items() if n >= GLIDE_SHARED_CPU_RESERVATION), None)
                    if host is None:
                        break
                    folder = attempt_directory(root, task)
                    folder.mkdir(parents=True, exist_ok=True)
                    work_started(f'Glide shared pool: submitting task {task["id"]}, reference {task["reference_id"]}, '
                                 f'compounds={len(set(task["payload"]["ids"]))}.')
                    prepared = retry._helper(args, folder, dict(action='prepare', payload=task['payload'], output=str(folder.resolve())))
                    c.submit_glide(args.schrodinger, Path(prepared['input']), folder, host+':1',
                                   memory_mb=GLIDE_SLURM_MEMORY_MB if getattr(args, 'use_slurm', False) else None,
                                   submitter=launch)
                    hosts[host] -= GLIDE_SHARED_CPU_RESERVATION
        except SubmissionBlocked as exc:
            print(f'Glide shared submissions pending: {exc}', flush=True)
        finally:
            done.set()
            pending = future.result()
            if isinstance(legacy_jobs, list):
                legacy_jobs[:] = pending


def service(args, iteration, root, merger, merge_best, legacy_jobs=(), extractor=None, collect_legacy=None):
    """Keep the stage worker alive to replenish slots without reinitializing it."""
    pending = list(legacy_jobs)
    capacity_hint = []
    tail_started = None
    while True:
        service_pass(args, iteration, root, merger, merge_best, pending, extractor, collect_legacy, capacity_hint)
        counts = database.glide_queue_counts(args.db_path, iteration, time.time())
        if not counts['pending'] and not counts['running'] and not pending:
            return
        total = counts['pending']+counts['running']+counts['done']
        if total and counts['done']/total >= getattr(args, 'completion_fraction', .95):
            tail_started = tail_started or time.monotonic()
        if tail_started is not None and time.monotonic()-tail_started >= getattr(args, 'tail_timeout',300):
            print('Glide shared tail: leaving saved jobs/queue for the next worker pass.', flush=True)
            return
        print(f'Glide shared queue: pending={counts["pending"]}, running={counts["running"]}, done={counts["done"]}.', flush=True)
        time.sleep(getattr(args,'poll_interval',30))
