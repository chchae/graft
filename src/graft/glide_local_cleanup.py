"""Publish verified Prime Glide results locally before retiring shared work."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from graft import database, glide_local
from graft.md_gbsa_native import save
from graft.paths import stage_directory
from graft import schrodinger_guard as guard


def receipt_path(directory):
    return directory.with_name(directory.name+'.prime-cleanup.json')


def cleanup_completed(directory):
    path = receipt_path(directory)
    return (path.is_file() and json.loads(path.read_text()).get('cleaned', False)
            and directory.is_dir() and not directory.is_symlink())


def pending_cleanup(directory):
    return ((directory/'prime-backend.json').is_file() or receipt_path(directory).is_file()) and not cleanup_completed(directory)


def pending_iteration(output):
    iterations = []
    for folder in Path(output).glob('iter*'):
        match = re.fullmatch(r'iter([0-9]+)', folder.name)
        if match:
            iteration = int(match[1])
            if pending_cleanup(stage_directory(output, iteration, 'glide')):
                iterations.append(iteration)
    return min(iterations, default=None)


def manifest(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Glide publication refuses symlinked shared artifacts')
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(chunk)
            result[str(path.relative_to(directory))] = digest.hexdigest()
    return result


def _verify(directory, expected):
    if manifest(directory) != expected:
        raise RuntimeError('Local Glide result checksums differ; shared work retained')


def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_copy(directory):
    for path in directory.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in directory.rglob('*') if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        _sync_directory(path)
    _sync_directory(directory)


def _finish(directory, receipt):
    settings = receipt['settings']
    shared = Path(settings['grid']).parent
    source = shared/'06-glide'
    tombstone = shared.with_name(shared.name+'.collected')
    temporary = Path(receipt['temporary'])
    if (not re.fullmatch(r'graft-glide-[0-9a-f]{32}', shared.name)
            or temporary.parent != directory.parent
            or not temporary.name.startswith('.'+directory.name+'.collect-')):
        raise RuntimeError('Invalid Prime Glide cleanup ownership/path')
    if receipt.get('cleaned'):
        if not directory.is_dir() or directory.is_symlink():
            raise RuntimeError('Collected local Glide results missing')
        return
    # A durable receipt is written only after every native tree is terminal,
    # SQLite barriers clear, and the complete local copy matches the source.
    if directory.is_symlink():
        if directory.resolve() != source.resolve():
            raise RuntimeError('Foreign Glide stage link; cleanup withheld')
        _verify(temporary, receipt['manifest'])
        directory.unlink()
    if not directory.exists():
        _verify(temporary, receipt['manifest'])
        temporary.rename(directory)
        _sync_directory(directory.parent)
    _verify(directory, receipt['manifest'])
    if shared.exists():
        if json.loads((source/'prime-backend.json').read_text()) != settings:
            raise RuntimeError('Foreign shared Glide work; cleanup withheld')
        _verify(source, receipt['manifest'])
    # Keep a local read-only server monitor for cross-project exclusion after
    # deleting the shared client. It never depends on the removed work root.
    glide_local.register_monitor(settings['transport'], replace=settings['client'])
    if shared.exists():
        if tombstone.exists():
            raise RuntimeError('Ambiguous Prime Glide retirement directories')
        shared.rename(tombstone)
    if tombstone.exists():
        shutil.rmtree(tombstone)
    receipt['cleaned'] = True
    save(receipt_path(directory), receipt)
    _sync_directory(directory.parent)
    print(f'Glide: results preserved in {directory}; shared Prime work removed.', flush=True)


def resume_cleanup(args, iteration):
    directory = stage_directory(args.output, iteration, 'glide')
    path = receipt_path(directory)
    if not path.is_file():
        return False
    receipt = json.loads(path.read_text())
    if database.docking_completion_reason(args.db_path, iteration, args.target_count):
        raise RuntimeError('Collected Prime Glide iteration changed; explicit recovery required')
    _finish(directory, receipt)
    return True


def collect_and_cleanup(args):
    iteration = getattr(args, 'glide_local_iteration', None)
    if iteration is None:
        return
    directory = stage_directory(args.output, iteration, 'glide')
    if receipt_path(directory).exists():
        resume_cleanup(args, iteration)
        return
    if database.docking_completion_reason(args.db_path, iteration, args.target_count):
        return  # Results/queues/publication are not yet fully committed.
    settings = json.loads((directory/'prime-backend.json').read_text())
    source = directory.resolve()
    evidence = []
    for record in source.rglob('glide_job.json'):
        saved = json.loads(record.read_text())
        if not saved.get('job_id'):
            return  # An uncertain native launch must never be removed.
        jobs = guard.job_details(args.schrodinger, saved['job_id'], record.parent)
        if any(job.get('status') not in guard.TERMINAL for job in jobs):
            return
        evidence.extend(jobs)
    # Also fence orphaned live jobs whose parent identity was never checkpointed.
    remote_root = Path(settings['transport']['remote_root'])
    for job in guard.active_jobs(args.schrodinger):
        launch = job.get('spec', {}).get('launchParams', {}).get('launchDirectory')
        if launch and (Path(launch).is_relative_to(source)
                       or Path(launch).is_relative_to(remote_root)):
            return
        if any(str(root)+'/' in job.get('commandLine', '') for root in (source, remote_root)):
            return
    temporary = directory.with_name('.'+directory.name+'.collect-'+uuid.uuid4().hex)
    expected = manifest(source)
    shutil.copytree(source, temporary)
    _verify(temporary, expected)
    _sync_copy(temporary)
    receipt = dict(settings=settings, temporary=str(temporary), manifest=expected,
                   native=evidence, cleaned=False)
    save(receipt_path(directory), receipt)
    _sync_directory(directory.parent)
    _finish(directory, receipt)
