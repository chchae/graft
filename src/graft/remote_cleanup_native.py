"""Standalone post-commit cleanup adapter. Never launches scientific jobs."""
from contextlib import ExitStack
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import os
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def lock(stack, path):
    stream = stack.enter_context(path.open('a'))
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def owned_path(base, name):
    if base.is_symlink() or not base.is_absolute() or base.resolve() != base:
        raise RuntimeError('Cleanup base is not an owned real directory')
    path = base / name
    if path.is_symlink() or path.resolve() != path:
        raise RuntimeError('Cleanup target contains a symlink')
    return path


def cleanup_base(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError('Cleanup base must be an absolute owned directory')
    # Resolve home aliases on the remote host, never on the submitting client.
    return path.resolve()


def retire_tree(path):
    """Fence the owned tree while its controller locks are still held."""
    tombstone = path.with_name('.collected-' + path.name)
    if path.is_symlink() or tombstone.is_symlink():
        raise RuntimeError('Cleanup tombstone is a symlink')
    if path.exists():
        if tombstone.exists():
            raise RuntimeError('Cleanup target and tombstone both exist')
        path.rename(tombstone)
    return tombstone


def remove_tree(path):
    """Delete after closing controller files so NFS creates no busy .nfs files."""
    tombstone = retire_tree(path)
    if tombstone.exists():
        shutil.rmtree(tombstone)


def amber(request):
    base = cleanup_base(request['base'])
    name = request['run_id']
    if not re.fullmatch(r'run-[0-9a-f]{32}', name):
        raise RuntimeError('Invalid owned Amber run')
    root = owned_path(base, name)
    receipt = base / ('.cleanup-' + name + '.json')
    if receipt.is_symlink():
        raise RuntimeError('Cleanup receipt is a symlink')
    # Receipt is durable BEFORE deletion; it authorizes only this exact tree.
    if receipt.exists():
        if json.loads(receipt.read_text()) != request:
            raise RuntimeError('Amber cleanup request changed')
    else:
        if digest(root / 'input.json') != request['input_sha256']:
            raise RuntimeError('Amber input ownership differs')
        if request['mode'] not in ('slurm_array_v1', 'slurm_single_v1', 'ssh'):
            raise RuntimeError('Unknown Amber cleanup mode')
        config = json.loads((root / 'input.json').read_text())
        ids = [entry['id'] for entry in config['entries']]
        if ids != request['ids'] or set(map(int, request['successful'])) - set(ids):
            raise RuntimeError('Amber cleanup identities differ')
        if request['mode'].startswith('slurm_'):
            import importlib.util
            spec = importlib.util.spec_from_file_location('amber_control', root / 'amber_slurm_control.py')
            control = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(control)
            journal = json.loads((root / 'slurm-submission.json').read_text())
            if journal.get('job_id') != request['job_id'] or journal.get('launching'):
                raise RuntimeError('Amber native identity differs')
            state = control.status(request['job_id'], len(ids) if request['mode'] == 'slurm_array_v1' else None)
            if state['active']:
                raise RuntimeError('Amber native work is active')
        with ExitStack() as stack:
            lock(stack, root / 'slurm-submit.lock')
            tasks = ([owned_path(root / 'tasks', str(i)) for i in range(len(ids))]
                     if request['mode'] == 'slurm_array_v1' else [root])
            for task in tasks:
                lock(stack, task / 'run.lock')
            results = {}
            for task in tasks:
                for result in (task / 'results').glob('CMPID_*.json'):
                    value = json.loads(result.read_text())
                    results[value['id']] = value
            for cid, score in request['successful'].items():
                if results.get(int(cid), {}).get('score') != score:
                    raise RuntimeError('Amber cleanup score differs from committed result')
            save(receipt, request)
    if len(request['successful']) == len(request['ids']):
        remove_tree(root)
    else:
        # Retain failed tasks and shared inputs for diagnosis/retry.
        for index, cid in enumerate(request['ids']):
            if str(cid) in request['successful']:
                if request['mode'] == 'slurm_array_v1':
                    task = root / 'tasks' / str(index)
                    # Keep task completion markers usable by explicit retry
                    # diagnostics for the failed siblings in this array.
                    for child in list(task.iterdir()):
                        if child.name in ('input.json', 'status.json', 'results', 'run.lock', 'calculation.log'):
                            continue
                        if child.is_dir() and not child.is_symlink():
                            remove_tree(child)
                        else:
                            child.unlink()
                remove_tree(root / f'CMPID_{cid}')
    return {'status': 'done', 'request': request}


def md(request):
    base = cleanup_base(request['base'])
    cid = request['id']
    if type(cid) is not int or cid < 1:
        raise RuntimeError('Invalid MD compound identity')
    root = owned_path(base, f'CMPID_{cid}')
    if json.loads((base / 'ready.json').read_text()) != request['selection']:
        raise RuntimeError('MD cleanup selection ownership differs')
    if (cid not in request['selection']['ids'] or
            len(request['native']) != request['selection']['settings']['replicates']):
        raise RuntimeError('MD cleanup replicas/identity differ from selection')
    receipt = base / f'.cleanup-CMPID_{cid}.json'
    if receipt.is_symlink():
        raise RuntimeError('Cleanup receipt is a symlink')
    if receipt.exists():
        if json.loads(receipt.read_text()) != request:
            raise RuntimeError('MD cleanup request changed')
        if request['action'] == 'delete':
            remove_tree(root)
            return {'status': 'done', 'request': request}
        raise RuntimeError('MD archive requested after cleanup intent')
    with ExitStack() as stack:
        for replica, record in enumerate(request['native'], 1):
            folder = owned_path(root, f'replica{replica:02d}')
            lock(stack, folder / 'controller.lock')
            journal = json.loads((folder / 'md/md.job.json').read_text())
            if journal != record['md'] or not journal.get('job_id') or not journal.get('downloaded'):
                raise RuntimeError('MD cleanup journal differs from committed result')
            suite = request['selection']['settings']['suite']
            output = subprocess.check_output([suite + '/jsc', 'info', '--json', journal['job_id']], text=True, timeout=30)
            jobs = [json.loads(line) for line in output.splitlines() if line.strip()]
            if not any(j.get('jobId') == journal['job_id'] for j in jobs) or any(j.get('status') != 'DONE' for j in jobs):
                raise RuntimeError('MD parent/children are active, failed or uncertain')
        archive = root / 'cleanup-evidence.tar'
        if request['action'] == 'archive':
            pending = archive.with_suffix('.pending')
            with tarfile.open(pending, 'w') as stream:
                # Preserve inputs, final system coordinates, logs and journals;
                # deliberately exclude trajectory, checkpoints and scratch files.
                for folder in sorted(root.glob('replica*')):
                    for path in sorted(folder.rglob('*')):
                        if path.is_file() and not path.is_symlink() and (
                                path.suffix in ('.json', '.log', '.msj', '.cfg') or
                                path.name in ('complex.maegz', 'md-out.cms', 'prime_mmgbsa-out.maegz')):
                            stream.add(path, arcname=str(path.relative_to(root)), recursive=False)
            pending.replace(archive)
            return {'status': 'archived', 'sha256': digest(archive)}
        if request['action'] != 'delete' or digest(archive) != request['archive_sha256']:
            raise RuntimeError('MD evidence was not collected')
        save(receipt, request)
        retire_tree(root)
    # NFS keeps unlinked open controller files as .nfs* entries. Retire the
    # original path under lock, then close every lock before removing its tree.
    remove_tree(root)
    return {'status': 'done', 'request': request}


def md_run(request):
    """Retire only a UUID run whose every selected compound was collected."""
    root = cleanup_base(request['base'])
    if not re.fullmatch(r'[0-9a-f]{32}', root.name):
        raise RuntimeError('Invalid owned MD run')
    root = owned_path(root.parent, root.name)
    receipt = root.parent / ('.cleanup-md-run-' + root.name + '.json')
    if receipt.is_symlink():
        raise RuntimeError('MD run receipt is a symlink')
    if receipt.exists():
        if json.loads(receipt.read_text()) != request or request['action'] != 'delete':
            raise RuntimeError('MD run cleanup request changed')
        remove_tree(root)
        return {'status': 'done', 'request': request}
    selection = request['selection']
    if (selection['settings']['root'] != request['base'] or not selection['ids'] or
            json.loads((root / 'ready.json').read_text()) != selection or
            set(request['receipts']) != {str(cid) for cid in selection['ids']}):
        raise RuntimeError('MD run cleanup ownership differs')
    with ExitStack() as stack:
        lock(stack, root / 'cleanup-run.lock')
        allowed = {'ready.json', 'native.py', 'prime_final.maegz', 'control',
                   'cleanup-run.lock', 'cleanup-run-evidence.tar', 'cleanup-run-evidence.pending'}
        allowed.update(f'.cleanup-CMPID_{cid}.json' for cid in selection['ids'])
        for path in root.iterdir():
            if path.name not in allowed or path.is_symlink() or (path.is_dir() and path.name != 'control'):
                raise RuntimeError('MD run has uncollected, foreign or uncertain work')
        for cid in selection['ids']:
            path = root / f'.cleanup-CMPID_{cid}.json'
            proof = json.loads(path.read_text())
            hashed = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
            if (hashed != request['receipts'][str(cid)] or proof['selection'] != selection
                    or proof['id'] != cid or proof['action'] != 'delete'):
                raise RuntimeError('MD run compound cleanup receipt differs')
            for record in proof['native']:
                identity = record['md']['job_id']
                output = subprocess.check_output([
                    selection['settings']['suite'] + '/jsc', 'info', '--json', identity],
                    text=True, timeout=30)
                jobs = [json.loads(line) for line in output.splitlines() if line.strip()]
                if (not any(j.get('jobId') == identity for j in jobs)
                        or any(j.get('status') != 'DONE' for j in jobs)):
                    raise RuntimeError('MD run still has active, failed or uncertain native work')
        archive = root / 'cleanup-run-evidence.tar'
        if request['action'] == 'archive':
            pending = archive.with_suffix('.pending')
            with tarfile.open(pending, 'w') as stream:
                for path in sorted(root.rglob('*')):
                    if path.is_symlink():
                        raise RuntimeError('MD run archive contains a symlink')
                    if path.is_file() and path not in (archive, pending):
                        stream.add(path, arcname=str(path.relative_to(root)), recursive=False)
            pending.replace(archive)
            return {'status': 'archived', 'sha256': digest(archive)}
        if request['action'] != 'delete' or digest(archive) != request['archive_sha256']:
            raise RuntimeError('MD run evidence was not collected')
        current = {str(path.relative_to(root)): path for path in root.rglob('*')
                   if path.is_file() and path not in (archive, archive.with_suffix('.pending'))}
        with tarfile.open(archive) as stream:
            members = stream.getmembers()
            if len(members) != len(current) or {m.name for m in members} != set(current):
                raise RuntimeError('MD run files changed after archival')
            for member in members:
                path = current[member.name]
                if (not member.isfile() or path.is_symlink() or
                        hashlib.sha256(stream.extractfile(member).read()).hexdigest() != digest(path)):
                    raise RuntimeError('MD run files changed after archival')
        save(receipt, request)
        retire_tree(root)
    remove_tree(root)
    return {'status': 'done', 'request': request}


if __name__ == '__main__':
    request = json.loads(sys.stdin.read())
    print(json.dumps({'amber': amber, 'md': md, 'md_run': md_run}[request['kind']](request)))
