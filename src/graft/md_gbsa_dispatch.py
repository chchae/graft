"""Operational MD admission adapter; import the run's frozen scientific helper."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path


class MDReady(Exception):
    pass


def poll_md(native, root, suite, *, start=False):
    """Check native parent/children without downloading or extracting frames."""
    journal = root/'md/md.job.json'
    if not journal.exists() and (root/'md').exists():
        if any(path.name != 'md.msj' for path in (root/'md').iterdir()):
            return dict(status='pending', phase='md', reason='Unjournaled MD artifacts; GPU reservation retained')
    if not journal.exists() and not start:
        return dict(status='unstarted', phase='md')
    original = native.poll_job

    def md_job(folder, phase, command, suite):
        if phase != 'md':
            raise RuntimeError('MD admission cannot submit Prime work')
        record = json.loads(journal.read_text()) if journal.exists() else None
        if record is not None and record['command'] != command:
            raise RuntimeError('Saved native command differs; submission retained')
        if record is None or not record.get('job_id'):
            if not start:
                return dict(status='pending', reason='Uncertain MD launch; GPU reservation retained', native=record)
            state = original(folder, phase, command, suite)
        else:
            rows = native.native_rows(suite, record['job_id'])
            if not rows or not any(row['jobId'] == record['job_id'] for row in rows):
                raise RuntimeError('MD native parent missing; GPU reservation retained')
            if any(row.get('status') not in native.TERMINAL for row in rows):
                return dict(status='running', native=record, jobs=rows)
            parent = next(row for row in rows if row['jobId'] == record['job_id'])
            state = (dict(status='done') if parent['status'] == 'DONE' else
                     dict(status='failed', error=f"Native MD ended {parent['status']}; checkpoint and JobId retained",
                          native=record, jobs=rows))
        if state['status'] == 'done':
            raise MDReady
        return state

    native.poll_job = md_job
    try:
        # frames_only bypasses ensemble-result cache; MDReady stops execution
        # before frame extraction, file collection or any Prime submission.
        return native.poll(root, suite, frames_only=True)
    except MDReady:
        return dict(status='pending', phase='frames', reason='Native MD complete; frames not yet processed')
    finally:
        native.poll_job = original


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['md-status', 'md-start'])
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1]
    relative = args.root.resolve().relative_to(base)
    if len(relative.parts) != 2 or not relative.parts[0].startswith('CMPID_') or not relative.parts[1].startswith('replica'):
        raise RuntimeError('MD admission task is outside the frozen run')
    settings = json.loads((base/'ready.json').read_text())['settings']
    helper = Path(settings['helper'])
    if helper.resolve() != base/'native.py' or hashlib.sha256(helper.read_bytes()).hexdigest() != settings['helper_sha256']:
        raise RuntimeError('Frozen MD helper identity changed')
    spec = importlib.util.spec_from_file_location('graft_frozen_md', helper)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with (args.root/'controller.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = poll_md(native, args.root, Path(os.environ['SCHRODINGER']), start=args.action == 'md-start')
    print('GRAFT_MD='+json.dumps(result))


if __name__ == '__main__':
    main()
