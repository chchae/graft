"""Worker-owned frame preparation, native Prime batching and result collection."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import threading
import time

from graft import md_gbsa, md_gbsa_local as local, schrodinger_guard as guard

_IO_WORKERS = 4
_POLL_SECONDS = 5.


def run(args, iteration, settings, tasks, progress, status_file, directory,
        resolved, progress_lock, admission_errors):
    """Keep scientific I/O off the single SQLite/submission owner.

    Accumulate a full CPU wave across distinct compounds before a new native
    parent. Flush a smaller final wave once no more MD/frame inputs are coming.
    Existing bindings/journals always recover before considering a new batch.
    """
    remaining = {(cid, replica): (cid, replica, root) for cid, replica, root in tasks
                 if cid not in resolved}
    prepared = set()
    preparing, collecting = {}, {}
    archive = directory/'adam-results'
    next_poll = {}
    failures, failed_io = [], set()
    terminal = set()

    def accept(key, result):
        cid, replica = key
        if result.get('status') == 'done':
            protocol = dict(settings['protocol'],
                            seed=1+(settings['protocol']['seed']+replica-1)%2147483647)
            if result.get('id') != cid or result.get('protocol') != protocol:
                raise RuntimeError('Remote MD ensemble identity/protocol does not match the frozen task')
        md_gbsa._record_progress(progress, str(key), result, status_file, progress_lock)
        if result.get('status') in ('done', 'failed'):
            terminal.add(key)
            verified = all((cid, r) in terminal for r in range(settings['replicates']))
            if verified and md_gbsa.persist_ready_compound(args, iteration, cid, settings,
                    md_gbsa._progress_snapshot(progress, progress_lock), directory):
                resolved.add(cid)
            remaining.pop(key, None)
            prepared.discard(key)

    def binding(key):
        root = local.task_root(settings, *key)
        pool = root/'prime/pool.json'
        if pool.exists():
            return 'pool:'+json.loads(pool.read_text())['batch']
        if (root/'prime/prime.job.json').exists():
            return 'legacy:'+str(root)
        return None

    def identified_parent(key, token):
        root = local.task_root(settings, *key)
        member = root/'prime/prime.job.json'
        parent = (root.parents[1]/'pools'/token[5:]/'prime/prime.job.json'
                  if token.startswith('pool:') else member)
        if not member.exists() or not parent.exists():
            return False
        identity = json.loads(member.read_text()).get('job_id')
        return bool(identity) and identity == json.loads(parent.read_text()).get('job_id')

    # A per-compound controller runs in only one I/O future at a time. Native
    # submissions and all SQLite mutations stay in this thread, under the guard.
    with ThreadPoolExecutor(max_workers=_IO_WORKERS, thread_name_prefix='graft-md-frames') as frames, \
         ThreadPoolExecutor(max_workers=_IO_WORKERS, thread_name_prefix='graft-md-collect') as outputs:
        try:
            while remaining:
                if admission_errors:
                    raise admission_errors[0]
                for futures, is_preparation in ((collecting, False), (preparing, True)):
                    for future, key in list(futures.items()):
                        if not future.done():
                            continue
                        del futures[future]
                        try:
                            result = future.result()
                            if is_preparation and result.get('status') == 'done':
                                raise RuntimeError('Frame preparation cannot establish ensemble completion')
                        except Exception as exc:
                            failures.append(exc)
                            failed_io.add(key)
                            continue
                        if is_preparation and result.get('frames_prepared'):
                            prepared.add(key)
                        accept(key, result)
                snapshot = md_gbsa._progress_snapshot(progress, progress_lock)
                busy = set(preparing.values()) | set(collecting.values())
                for key, task in list(remaining.items()):
                    if failures or key in prepared or key in busy:
                        continue
                    prior = snapshot[str(key)]
                    if prior.get('phase') == 'md':
                        if prior.get('status') == 'failed':
                            accept(key, prior)
                        continue
                    if len(preparing) < _IO_WORKERS:
                        preparing[frames.submit(local.prepare_frames, settings, *task)] = key

                # A published result is only a collection candidate. Each I/O
                # task rechecks native parent/children and copies coordinates;
                # only then may the owner commit its validated full ensemble.
                for key in remaining:
                    if key not in prepared or key in collecting.values() or key in failed_io:
                        continue
                    root = local.task_root(settings, *key)
                    if (root/'result.json').exists() and len(collecting) < _IO_WORKERS:
                        collecting[outputs.submit(local.collect_result, settings, *key, archive)] = key

                if failures:
                    # No further submissions or preparation after an I/O error.
                    # Drain verified ready outputs so an unrelated failure does
                    # not prevent their immediate SQLite persistence.
                    if not preparing and not collecting:
                        raise failures[0]
                    wait(list(preparing)+list(collecting), timeout=.2, return_when=FIRST_COMPLETED)
                    continue

                bound, unbound = [], []
                for key in remaining:
                    if key not in prepared or key in collecting.values():
                        continue
                    if (local.task_root(settings, *key)/'result.json').exists():
                        continue  # Wait for a collection slot, never poll/submit again.
                    token = binding(key)
                    (bound if token else unbound).append((key, token))
                incoming = bool(preparing) or any(key not in prepared and key not in collecting.values()
                                                  for key in remaining)
                distinct = {key[0] for key, _ in unbound}
                ready_frames = len(distinct)*settings['protocol']['snapshots']
                candidate = next(((key, token) for key, token in bound
                                  if time.monotonic() >= next_poll.get(token, 0)), None)
                if candidate is None and unbound and (ready_frames >= settings['local_compute']['slots']
                                                       or not incoming):
                    key, _ = unbound[0]
                    if time.monotonic() >= next_poll.get('new', 0):
                        candidate = key, 'new'
                if candidate is not None:
                    key, token = candidate
                    cid, replica, root = remaining[key]
                    try:
                        if token != 'new' and identified_parent(key, token):
                            # The remote adapter enforces an identified journal
                            # at the actual native call. No submission is possible,
                            # so unrelated GPU/global admission queries are avoided.
                            result = local.poll(settings, cid, replica, root, archive,
                                prepared=True, collect=False, allow_submit=False, existing=True)
                        else:
                            result = None
                        if result is None:
                            with guard.submission_guard(args.schrodinger, 'prime_gbsa', md_controller=True):
                                jobs = guard.active_jobs(args.schrodinger)
                                if any(guard.job_kind(j) == 'prime_gbsa' and not j.get('graft_md_remote')
                                       for j in jobs):
                                    print('MD-GBSA waiting for other Prime work.')
                                    return
                                result = local.poll(settings, cid, replica, root, archive,
                                    prepared=True, collect=False,
                                    allow_submit=not any(guard.job_kind(j) == 'prime_gbsa' and
                                        not j.get('graft_md_local_compute') for j in jobs))
                    except guard.SubmissionBlocked as exc:
                        print(str(exc))
                        return
                    next_poll[token] = time.monotonic()+_POLL_SECONDS
                    actual = binding(key)
                    if actual:
                        next_poll[actual] = next_poll[token]
                        if token == 'new' and actual.startswith('pool:'):
                            request = local.task_root(settings, cid, replica).parents[1]/'pools'/actual[5:]/'request.json'
                            if request.exists():
                                batch = json.loads(request.read_text())
                                print(f'MD-GBSA Prime batch: compounds={len(batch["members"])}, '
                                      f'snapshots={sum(m["snapshots"] for m in batch["members"])}, '
                                      f'cpu_slots={batch["slots"]}.', flush=True)
                    if result.get('status') == 'done':
                        if len(collecting) < _IO_WORKERS:
                            collecting[outputs.submit(local.collect_result, settings,
                                                       cid, replica, archive, result)] = key
                    else:
                        accept(key, result)
                if not remaining:
                    break
                if not prepared and not preparing and not collecting:
                    return  # GPU-only work resumes through normal driver polling.
                futures = list(preparing)+list(collecting)
                if futures:
                    wait(futures, timeout=.2, return_when=FIRST_COMPLETED)
                else:
                    threading.Event().wait(.2)
        finally:
            for future in list(preparing)+list(collecting):
                future.cancel()
