"""Worker-owned cleanup after durable scientific result collection."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess

from graft import database


def command():
    source = Path(__file__).with_name('remote_cleanup_native.py').read_text()
    return shlex.join(['python3', '-c', source])


def amber(args, iteration, directory, record):
    from graft import amber_gbsa
    folder = directory / record['run_id']
    intent = folder / 'remote-cleanup.json'
    if intent.exists():
        request = json.loads(intent.read_text())['request']
    else:
        snapshot = json.loads((directory / 'remote_snapshot.json').read_text())
        scores = database.amber_cleanup_scores(args.db_path, iteration, record['compound_ids'])
        successful = {str(r['id']): r['score'] for r in snapshot['results']
                      if r.get('score') is not None and scores.get(r['id']) == r['score']}
        saved_input = json.loads((folder / 'input.json').read_text())
        ids = [entry['id'] for entry in saved_input['entries']]
        if set(ids) != set(record['compound_ids']) or len(ids) != len(record['compound_ids']):
            raise RuntimeError('Amber cleanup input identities differ')
        request = dict(kind='amber', base=record['settings']['remote_work_dir'],
                       run_id=record['run_id'], ids=ids,
                       mode=record.get('mode', 'ssh'), job_id=record.get('slurm_job_id'),
                       input_sha256=hashlib.sha256((folder / 'input.json').read_bytes()).hexdigest(),
                       successful=successful)
        if PurePosixPath(request['base']) / record['run_id'] != PurePosixPath(record['remote_dir']):
            raise RuntimeError('Amber cleanup path differs from saved owner')
        if not successful:
            return  # Failed work is deliberately retained.
        amber_gbsa.save(intent, dict(request=request, completed=False))
    scores = database.amber_cleanup_scores(args.db_path, iteration, record['compound_ids'])
    if any(scores.get(int(cid)) != score for cid, score in request['successful'].items()):
        raise RuntimeError('Amber cleanup requires committed matching scores')
    result = amber_gbsa.ssh(record['settings'], command(), input=json.dumps(request),
                            capture_output=True, text=True)
    if json.loads(result.stdout) != dict(status='done', request=request):
        raise RuntimeError('Amber cleanup not acknowledged')
    amber_gbsa.save(intent, dict(request=request, completed=True))


def md(args, iteration, settings, directory):
    from graft import md_gbsa
    from graft.md_gbsa_native import save
    for cid in database.pending_md_remote_cleanup_ids(args.db_path, iteration):
        result = database.md_cleanup_result(args.db_path, iteration, cid)
        if not result or not result.get('native'):
            raise RuntimeError('MD cleanup requires committed native evidence')
        if settings.get('local_compute') and cid in database.pending_md_local_cleanup_ids(args.db_path, iteration):
            continue  # Adam coordinates and verification must be collected first.
        folder = directory / 'remote-cleanup' / f'CMPID_{cid}'
        folder.mkdir(parents=True, exist_ok=True)
        intent = folder / 'cleanup.json'
        if intent.exists():
            request = json.loads(intent.read_text())['request']
        else:
            request = dict(kind='md', action='archive', base=settings['root'], id=cid,
                           native=result['native'], selection=database.md_gbsa_selection(args.db_path, iteration))
            response = json.loads(md_gbsa.ssh(settings['ssh_host'], command(), input=json.dumps(request)))
            if response.get('status') != 'archived':
                raise RuntimeError('MD evidence archive not ready')
            archive = folder / 'evidence.tar'
            temporary = folder / 'evidence.tar.pending'
            remote = settings['root'] + f'/CMPID_{cid}/cleanup-evidence.tar'
            with temporary.open('wb') as stream:
                subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                settings['ssh_host'], 'cat -- ' + shlex.quote(remote)],
                               stdout=stream, stderr=subprocess.PIPE, check=True, timeout=240)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != response['sha256']:
                raise RuntimeError('MD evidence archive checksum differs; trajectory retained')
            temporary.replace(archive)
            request.update(action='delete', archive_sha256=response['sha256'])
            save(intent, dict(request=request, completed=False))
        if hashlib.sha256((folder / 'evidence.tar').read_bytes()).hexdigest() != request['archive_sha256']:
            raise RuntimeError('Collected MD archive changed; cleanup withheld')
        response = json.loads(md_gbsa.ssh(settings['ssh_host'], command(), input=json.dumps(request)))
        if response != dict(status='done', request=request):
            raise RuntimeError('MD cleanup not acknowledged')
        save(intent, dict(request=request, completed=True))
        database.finish_md_remote_cleanup(args.db_path, iteration, cid)


def md_run(args, iteration, settings, directory):
    """Remove a fully collected MD run, retaining durable evidence locally.

    Partial failures keep the run for diagnosis. Historical scores without
    individual post-commit cleanup receipts do not enroll an old run.
    """
    from graft import md_gbsa
    from graft.md_gbsa_native import save
    saved = database.md_run_cleanup_record(args.db_path, iteration)
    if saved and saved['completed']:
        return
    folder = directory / 'remote-cleanup'
    if saved is None:
        if not database.md_run_cleanup_ready(args.db_path, iteration):
            return
        selection = database.md_gbsa_selection(args.db_path, iteration)
        receipts = {}
        for cid in selection['ids']:
            intent = json.loads((folder / f'CMPID_{cid}' / 'cleanup.json').read_text())
            request = intent['request']
            if not intent['completed'] or request['selection'] != selection:
                raise RuntimeError('MD run cleanup requires matching compound receipts')
            archive = folder / f'CMPID_{cid}' / 'evidence.tar'
            if hashlib.sha256(archive.read_bytes()).hexdigest() != request['archive_sha256']:
                raise RuntimeError('Collected MD evidence changed; run retained')
            receipts[str(cid)] = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        request = dict(kind='md_run', action='archive', base=settings['root'],
                       selection=selection, receipts=receipts)
        database.begin_md_run_cleanup(args.db_path, iteration, request)
    else:
        request = saved['request']
    if request['selection']['settings'] != settings:
        raise RuntimeError('MD run cleanup settings changed')
    intent = folder / 'run-cleanup.json'
    if intent.exists():
        deleting = json.loads(intent.read_text())['request']
        if {k: deleting[k] for k in request} != dict(request, action='delete'):
            raise RuntimeError('MD run cleanup receipt changed')
    else:
        response = json.loads(md_gbsa.ssh(settings['ssh_host'], command(), input=json.dumps(request)))
        if response.get('status') != 'archived':
            raise RuntimeError('MD run evidence archive not ready')
        temporary = folder / 'run-evidence.tar.pending'
        with temporary.open('wb') as stream:
            remote = settings['root'] + '/cleanup-run-evidence.tar'
            subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                            settings['ssh_host'], 'cat -- ' + shlex.quote(remote)],
                           stdout=stream, stderr=subprocess.PIPE, check=True, timeout=240)
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != response['sha256']:
            raise RuntimeError('MD run evidence checksum differs; run retained')
        temporary.replace(folder / 'run-evidence.tar')
        deleting = dict(request, action='delete', archive_sha256=response['sha256'])
        save(intent, dict(request=deleting, completed=False))
    if hashlib.sha256((folder / 'run-evidence.tar').read_bytes()).hexdigest() != deleting['archive_sha256']:
        raise RuntimeError('Collected MD run evidence changed; deletion withheld')
    response = json.loads(md_gbsa.ssh(settings['ssh_host'], command(), input=json.dumps(deleting)))
    if response != dict(status='done', request=deleting):
        raise RuntimeError('MD run cleanup not acknowledged')
    save(intent, dict(request=deleting, completed=True))
    # Keep the SQLite completion gate closed until both remote and shared
    # working trees have been retired, including crashes between these steps.
    from graft import schrodinger_guard
    schrodinger_guard.unregister_md_remote(settings)
    if settings.get('local_compute'):
        from graft import md_gbsa_local
        md_gbsa_local.finish_cleanup(settings, request['selection']['ids'])
    database.finish_md_run_cleanup(args.db_path, iteration, request)
