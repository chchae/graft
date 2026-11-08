"""Track native Glide identity; cached output never proves a live job finished."""
import json
import hashlib
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from graft.stages._logging import detail

from graft.schrodinger_guard import (active_jobs, belongs_to_directory,
    job_details, job_kind, submission_guard, TERMINAL)


def record_file(directory):
    return Path(directory) / 'glide_job.json'


def load_record(directory):
    path = record_file(directory)
    return json.loads(path.read_text()) if path.exists() else None


def save_record(directory, record):
    path = record_file(directory)
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(record))
    pending.replace(path)


def discard_terminal_results(directory):
    """Caller has verified termination; retain JobId evidence and clear old results."""
    directory = Path(directory)
    if record_file(directory).exists():
        record_file(directory).replace(directory / f'glide_job.previous-{time.time_ns()}.json')
    for pattern in ('glide*.log', '*_lib.maegz', 'best_poses.maegz', 'docking_scores.tsv'):
        for path in directory.glob(pattern):
            if path.is_file():
                path.unlink()
    print(f'Removed previous Glide results in {directory.name}; starting a fresh calculation.', flush=True)


def enforce_timeout(suite, directory, record, timeout_seconds):
    """Stop a timed-out native tree; retain claims until every job terminates."""
    if not record or not record.get('job_id'):
        return None
    if timeout_seconds <= 0 and not record.get('timeout_requested_at'):
        return None
    jobs = job_details(suite, record['job_id'], directory)
    parent = next(j for j in jobs if j['jobId'] == record['job_id'])
    running = [j['jobId'] for j in jobs if j.get('status') not in TERMINAL]
    if not running:
        return 'STOPPED' if record.get('timeout_requested_at') else None
    started = parent.get('timeStarted')
    try:
        started = datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp() if started else record.get('submitted_at')
    except (ValueError, TypeError):
        started = record.get('submitted_at')
    now = time.time()
    if not record.get('timeout_requested_at'):
        if started is None or now - started < timeout_seconds:
            return None
        # Persist intent before stopping; collect partial results only after
        # the native parent and every child have terminated.
        record['timeout_requested_at'] = now
        record['timeout_seconds'] = timeout_seconds
        save_record(directory, record)
    if now - record.get('timeout_stop_sent_at', 0) >= 30:
        # Include live children even if their parent has already terminated.
        subprocess.run([str(Path(suite) / 'jsc'), 'stop', *running],
                       cwd=directory, check=True, capture_output=True, text=True, timeout=30)
        record['timeout_stop_sent_at'] = now
        save_record(directory, record)
        print(f"Glide {record['job_id']}: {record['timeout_seconds']}s timeout; "
              'stop requested, waiting for parent/children to terminate.', flush=True)
    return 'RUNNING'


def group_status(suite, directory, log_file, output_file, live=None, timeout_seconds=0):
    """Return a native status, UNKNOWN, or None for an unsubmitted group."""
    directory = Path(directory)
    live = active_jobs(suite) if live is None else live
    matches = [j for j in live if job_kind(j) == 'glide' and
               belongs_to_directory(j, directory)]
    record = load_record(directory)
    if matches:
        parents = [j for j in matches if not j.get('parentJobId')]
        if parents:
            # Recover a legacy/duplicate launch even if the local log is older.
            parent = max(parents, key=lambda j: j.get('timeCreated', ''))
            if record is None or record.get('job_id') and record['job_id'] != parent['jobId']:
                record = {'job_id': parent['jobId'], 'recovered': True, 'downloaded': False}
                save_record(directory, record)
        timeout_status = enforce_timeout(suite, directory, record, timeout_seconds)
        if timeout_status is not None and timeout_status != 'STOPPED':
            return timeout_status
        if timeout_status != 'STOPPED':
            return 'RUNNING'
    if record is None and log_file.exists():
        match = re.search(r'^JobId\s*:\s*(\S+)', log_file.read_text(errors='replace'), re.MULTILINE)
        if match:
            record = {'job_id': match.group(1), 'recovered': True, 'downloaded': False}
            save_record(directory, record)
    if record is not None:
        if not record.get('job_id'):
            return 'UNKNOWN'  # Never repeat an uncertain launch.
        timeout_status = enforce_timeout(suite, directory, record, timeout_seconds)
        if timeout_status is not None:
            return timeout_status
        jobs = job_details(suite, record['job_id'], directory)
        if any(j.get('status') not in TERMINAL for j in jobs):
            return 'RUNNING'
        parent = next(j for j in jobs if j['jobId'] == record['job_id'])
        if parent['status'] == 'DONE' and not record.get('downloaded'):
            subprocess.run([str(Path(suite) / 'jsc'), 'download', '--cwd', record['job_id']],
                           cwd=directory, check=True, capture_output=True, text=True, timeout=60)
            record['downloaded'] = True
            save_record(directory, record)
        return parent['status']
    # A result file without a submission identity cannot satisfy new work.
    if log_file.exists():
        return 'UNKNOWN'
    return None


def timeout_pose_files(suite, directory):
    """Download a stopped tree into fresh JobId-scoped directories.

    Never accept the group's potentially stale cached output as evidence.
    Download failures retain the durable recovery intent for the next pass.
    """
    directory = Path(directory)
    record = load_record(directory)
    if not record or not record.get('job_id') or not record.get('timeout_requested_at'):
        raise RuntimeError('Partial Glide recovery requires a saved timeout JobId')
    jobs = job_details(suite, record['job_id'], directory)
    if any(job.get('status') not in TERMINAL for job in jobs):
        raise RuntimeError('Partial Glide recovery waits for all parent/child jobs to terminate')
    if not any(job.get('jobId') == record['job_id'] for job in jobs):
        raise RuntimeError('Partial Glide recovery cannot verify its native parent')
    downloaded = set(record.get('timeout_downloaded_ids', []))
    files = []
    for job in jobs:
        identity = job['jobId']
        if not identity or Path(identity).name != identity or identity in {'.', '..'}:
            raise RuntimeError('Invalid native Glide output identity')
        folder = directory / 'timeout-results' / identity
        if (directory / 'timeout-results').is_symlink() or not folder.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError('Glide timeout output directory points outside its group')
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink():
            raise RuntimeError('Glide timeout output directory is a symlink')
        if identity not in downloaded:
            subprocess.run([str(Path(suite) / 'jsc'), 'download', '--cwd', identity],
                           cwd=folder, check=True, capture_output=True, text=True, timeout=60)
            downloaded.add(identity)
            record['timeout_downloaded_ids'] = sorted(downloaded)
            save_record(directory, record)
        for path in sorted(folder.rglob('*.maegz')):
            if path.name.endswith(('_lib.maegz', '_pv.maegz')):
                if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                    raise RuntimeError('Glide timeout output points outside its native directory')
                if path.is_file() and path.stat().st_size:
                    files.append(path)
    record['timeout_sources'] = [dict(path=str(path.relative_to(directory)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in files]
    record['timeout_native_status'] = next(job['status'] for job in jobs if job['jobId'] == record['job_id'])
    record['downloaded'] = True
    save_record(directory, record)
    return files


def submit(suite, command, directory):
    directory = Path(directory)
    with submission_guard(suite, 'glide'):
        # Recheck under the same submission mutex as prime_gbsa.
        live = active_jobs(suite)
        if any(job_kind(j) == 'glide' and belongs_to_directory(j, directory) for j in live):
            raise RuntimeError('Glide group already has a live external job; recover it instead')
        existing = load_record(directory)
        if existing and not existing.get('job_id'):
            raise RuntimeError('Glide launch has no saved JobId; reconcile before resubmission')
        if existing:
            if any(j.get('status') not in TERMINAL for j in job_details(suite, existing['job_id'], directory)):
                raise RuntimeError('Glide group saved job still running')
        archive = directory / f'previous-{time.time_ns()}'
        old = [path for path in directory.glob('glide*')
               if path.name != 'glide.in']
        old += [path for name in ('best_poses.maegz', 'docking_scores.tsv', 'glide_launch.log')
                if (path := directory / name).exists()]
        if old:
            archive.mkdir()
            for path in old:
                path.replace(archive / path.name)
        record = {'command': [str(x) for x in command], 'job_id': None,
                  'submitted_at': time.time(), 'downloaded': False}
        save_record(directory, record)
        result = subprocess.run(record['command'], cwd=directory, check=True,
                                capture_output=True, text=True)
        with (directory / 'glide_launch.log').open('a') as log:
            log.write(result.stdout)
            log.write(result.stderr or '')
        detail(result.stdout, end='', flush=True)
        if result.stderr:
            detail(result.stderr, end='', flush=True)
        match = re.search(r'^JobId:\s*(\S+)', result.stdout, re.MULTILINE | re.IGNORECASE)
        if not match:
            raise RuntimeError('Glide submission returned no JobId; reconcile before retrying')
        record['job_id'] = match.group(1)
        save_record(directory, record)
        return record['job_id']
