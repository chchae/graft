"""One local result writer; SQLite remains authoritative for claims and cursors."""
import hashlib
import json
import os
from pathlib import Path
import signal

from graft import database
from graft.chemistry.combichem3d.logging_utils import logger


def _publish(path, value):
    from graft.stages.combichem_buffer import _sync_directory
    temporary = path.with_suffix('.ready.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    _sync_directory(path.parent)


def submit(buffer, state):
    """Publish an immutable completed prefix; wait for a durable DB acknowledgement."""
    from graft.stages.combichem_buffer import _sync_directory
    ready = buffer.path.with_suffix('.ready')
    ready.parent.mkdir(exist_ok=True)
    data = buffer.path.read_bytes() if buffer.path.exists() else b''
    count = len(buffer.records)
    _publish(ready, dict(schema='graft_completed_batch_v1', batch=buffer.batch,
                        count=count, next_cursor=int(buffer.batch['cursor'])+count,
                        sha256=hashlib.sha256(data).hexdigest()))
    _sync_directory(ready.parent.parent)
    state['wake'].set()
    while ready.exists():
        if state['failed'].wait(.05):
            raise RuntimeError('Generation result writer failed; batch journal retained')
    if buffer.path.exists():
        raise RuntimeError('Generation writer acknowledgement left an uncommitted journal')
    buffer.records.clear()


def commit_ready(db, path):
    """Commit results and batch completion together, then acknowledge by removing files."""
    from graft.stages.combichem_buffer import _sync_directory
    manifest = json.loads(path.read_text())
    batch = manifest['batch']
    count, next_cursor = manifest['count'], manifest['next_cursor']
    if (manifest.get('schema') != 'graft_completed_batch_v1'
            or path.name != f"batch-{batch['id']}.ready"
            or type(count) is not int or type(next_cursor) is not int or count < 0
            or next_cursor != int(batch['cursor'])+count or next_cursor > int(batch['stop'])):
        raise ValueError('Invalid completed batch publication; files retained')
    journal = path.with_suffix('.jsonl')
    if journal.exists():
        data = journal.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['sha256'] or (data and not data.endswith(b'\n')):
            raise ValueError('Completed batch journal checksum/tail mismatch; files retained')
        rows = [json.loads(line) for line in data.splitlines()]
        if (len(rows) != count or any(r['batch'] != batch for r in rows)
                or [r['ordinal'] for r in rows] != list(range(int(batch['cursor']), next_cursor))):
            raise ValueError('Completed batch journal identity/ordinal mismatch; files retained')
        database.growth_save_attempts(db, batch,
            [(r['ordinal'], r['report'], r['payload']) for r in rows], finish=True)
        journal.unlink()
        _sync_directory(path.parent)
    elif count == 0:
        if manifest['sha256'] != hashlib.sha256(b'').hexdigest():
            raise ValueError('Invalid empty batch publication')
        database.growth_save_attempts(db, batch, [], finish=True)
    else:
        # A crash after commit+unlink but before acknowledgement must not
        # recalculate results or infer completion from the filesystem alone.
        database.growth_verify_batch_commit(db, batch, next_cursor)
    path.unlink()
    _sync_directory(path.parent)
    logger.debug('result writer committed batch=%s count=%s', batch['id'], count)


def run(db, state, supervisor_pid):
    """Persist only published batches while calculators run; recover orphans after exit."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    directory = Path(db).parent/'result-buffer'
    try:
        while True:
            if os.getppid() != supervisor_pid:
                raise RuntimeError('Generation supervisor exited; active journals retained')
            state['wake'].clear()
            for path in sorted(directory.glob('batch-*.ready')):
                commit_ready(db, path)
            if state['stop'].is_set():
                # stop is sent only after every calculator exited. Unpublished
                # prefixes from killed workers are now safe to recover too.
                from graft.stages.combichem_buffer import recover
                recover(db)
                return
            state['wake'].wait(.05)
    except BaseException:
        state['failed'].set()
        logger.exception('Generation result writer failed; journals and claims retained')
        try:
            database.growth_request_stop(db, 'worker_error')
        except Exception:
            logger.exception('Cannot persist generation writer failure')
        raise
