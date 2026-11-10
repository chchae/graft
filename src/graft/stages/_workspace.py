"""Clear orphaned iteration files before the first generation, preserving recovery."""
import json
from pathlib import Path
import re
import time
import csv
import shlex
from contextlib import ExitStack
import subprocess

from graft.paths import iteration_directory, stage_in_iteration
from graft import database
from graft import schrodinger_guard as guard


class WorkspaceReconciliationPending(RuntimeError):
    """Retain old work until its native/remote retirement is established."""


def reconciliation_error(exc):
    """Show remote diagnostics without echoing an inline SSH program."""
    if isinstance(exc, subprocess.CalledProcessError):
        detail = exc.stderr or exc.stdout
        if not detail:
            return f'External workspace inspection failed (exit status {exc.returncode}); recovery withheld'
    elif isinstance(exc, subprocess.TimeoutExpired):
        return 'External workspace inspection timed out; recovery withheld'
    else:
        detail = str(exc)
    if isinstance(detail, bytes):
        detail = detail.decode(errors='replace')
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    return lines[-1][-1000:] if lines else 'External workspace inspection failed; recovery withheld'


def reconcile_amber_job(path):
    from graft import amber_gbsa as amber, amber_slurm
    record = json.loads(path.read_text())
    if record.get('mode') not in (amber_slurm.ARRAY, amber_slurm.SINGLE) or not record.get('slurm_job_id'):
        raise RuntimeError('Unknown or uncertain Amber submission')
    native = amber_slurm.job_state(path.parent, record)
    if native['active']:
        raise RuntimeError(f"Amber Slurm job {record['slurm_job_id']} is active")
    remote = shlex.quote(record['remote_dir'])
    helper = 'amber_array.py' if record['mode'] == amber_slurm.ARRAY else 'worker.py'
    response = amber.ssh(record['settings'], f'python3 {remote}/{helper} collect {remote}',
                         capture_output=True, text=True, timeout=120)
    snapshot = json.loads(response.stdout)
    if snapshot['active']:
        raise RuntimeError('Amber remote execution locks remain held')
    if record['mode'] == amber_slurm.ARRAY and len(snapshot['tasks']) != len(record['compound_ids']):
        raise RuntimeError('Amber array task evidence is incomplete')
    return dict(native=native, snapshot=snapshot)


def prepare_iteration_directory(args, iteration):
    """Run under the generation stage lock, after preceding-iteration gates."""
    if iteration < 1:
        raise ValueError('Iteration workspace cleanup requires iteration >= 1')
    deleted = database.iteration_was_deleted(args.db_path, iteration)
    if database.iteration_has_saved_work(args.db_path, iteration) and not deleted:
        return None  # Resume exactly the saved iteration, including zero-row plans.
    with ExitStack() as locks:
        if deleted:
            for stage in ('druglike', 'synthetic_feasibility', 'ligprep', 'glide', 'amber_gbsa', 'prime_gbsa', 'md_gbsa', 'fep'):
                try:
                    locks.enter_context(database.stage_lock(args.db_path, stage))
                except RuntimeError as exc:
                    raise WorkspaceReconciliationPending(str(exc)) from exc
        return _prepare(args, iteration, deleted)


def _prepare(args, iteration, deleted):
    root = iteration_directory(Path(args.output), iteration)
    if root.is_symlink():
        raise RuntimeError(f'Iteration directory is a symlink; files retained: {root}')
    if not deleted and (not root.exists() or not any(root.iterdir())):
        database.mark_iteration_workspace_prepared(args.db_path, iteration)
        return None

    deleted_crem = deleted and database.crem_generation(args.db_path, iteration) is not None
    deletion_proof = None
    if deleted_crem:
        from graft.stages._deleted_crem import reconcile
        try:
            deletion_proof = reconcile(args, iteration, root)
        except Exception as exc:
            raise WorkspaceReconciliationPending(
                f'Deleted CReM recovery waiting: {reconciliation_error(exc)}') from exc

    # Configuration inputs must never be relocated while clearing working output.
    for key in ('db_path', 'toml_file', 'grid', 'reference_poses', 'libinvent_prior',
                'sidechain_library', 'prime_gbsa_receptor', 'amber_receptor', 'aizynth_config'):
        source = getattr(args, key, None)
        if source is not None and Path(source).resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f'Configured {key} is inside {root}; move the input before cleanup')

    paths = list(root.rglob('*'))
    files = [path for path in paths if path.is_file()]
    jobs = {}
    unidentified_logs = []
    remote_evidence = {}
    for path in files:
        if path.name == 'amber_job.json' or path.name.endswith('.remote-dir.txt'):
            try:
                if path.name == 'amber_job.json':
                    proof = reconcile_amber_job(path)
                else:
                    from graft.reinvent_remote import reconcile_remote_run
                    proof = reconcile_remote_run(path)
                remote_evidence[str(path.relative_to(root))] = proof
            except Exception as exc:
                detail = reconciliation_error(exc)
                raise WorkspaceReconciliationPending(
                    f'Remote work requires reconciliation; files retained: {path}: {detail}') from exc
        if path.name in ('ligprep_job.json', 'glide_job.json', 'prime_gbsa_job.json',
                         'ligprep_job.tmp', 'glide_job.tmp', 'prime_gbsa_job.tmp'):
            record = json.loads(path.read_text())
            if not record.get('job_id'):
                raise WorkspaceReconciliationPending(f'Saved or uncertain submission requires recovery; files retained: {path}')
            jobs[record['job_id']] = path.parent
        if path.suffix == '.log':
            ids = re.findall(r'^JobId\s*:\s*(\S+)', path.read_text(errors='replace'),
                             re.IGNORECASE | re.MULTILINE)
            jobs.update((job_id, path.parent) for job_id in ids)
            if not ids and (path.name.startswith('ligprep') or path.name.startswith('glide')
                            or path.name.startswith('prime_gbsa_') or path.name == 'glide_launch.log'):
                unidentified_logs.append(path)
    for path in unidentified_logs:
        if path.parent not in jobs.values():
            raise WorkspaceReconciliationPending(f'Calculation log has no recoverable JobId; files retained: {path}')

    # Check all native jobs before moving a single file, including children of
    # terminal parents. A lookup failure propagates without changing anything.
    directories = {root, *(path for path in paths if path.is_dir())}
    live = guard.active_jobs(args.schrodinger)
    if any(guard.belongs_to_directory(job, folder) for job in live for folder in directories):
        raise WorkspaceReconciliationPending(f'Iteration has live external jobs; files retained: {root}')
    for job in live:
        launch = job.get('spec', {}).get('launchParams', {}).get('launchDirectory')
        if launch and Path(launch).resolve().is_relative_to(root.resolve()):
            raise WorkspaceReconciliationPending(f'Iteration has live external jobs; files retained: {root}')
    for job_id, folder in jobs.items():
        details = guard.job_details(args.schrodinger, job_id, folder)
        if (not any(job.get('jobId') == job_id for job in details)
                or any(job.get('status') not in guard.TERMINAL for job in details)):
            raise WorkspaceReconciliationPending(f'Iteration job {job_id} is live or uncertain; files retained: {root}')

    archive_root = Path(args.output) / '.previous'
    if archive_root.is_symlink():
        raise RuntimeError(f'Iteration archive is a symlink; files retained: {archive_root}')
    archive_root.mkdir(exist_ok=True)
    archive = archive_root / f'{root.name}-{time.time_ns()}'
    report_ids = []
    report = stage_in_iteration(root, 'druglike') / 'druglike_selection.csv'
    if deleted and report.exists():
        with report.open(newline='') as stream:
            report_ids = [int(row['id']) for row in csv.DictReader(stream)]
    had_root = root.exists()
    if had_root:
        root.rename(archive)
    else:
        archive.mkdir()
    root.mkdir(parents=True)
    try:
        if remote_evidence:
            (archive / 'workspace_remote_reconciliation.json').write_text(
                json.dumps(remote_evidence, indent=2) + '\n')
        if deletion_proof is not None:
            (archive / 'deleted_crem_reconciliation.json').write_text(
                json.dumps(deletion_proof, indent=2) + '\n')
        if deleted:
            database.reset_deleted_iteration(args.db_path, iteration,
                                             archive / f'database-before-reset-{time.time_ns()}.sqlite', report_ids,
                                             allow_deleted_crem=deleted_crem,
                                             reconciled_md=(deletion_proof or {}).get('md', {}).get('selection'))
        else:
            database.mark_iteration_workspace_prepared(args.db_path, iteration)
    except BaseException as exc:
        # No worker can modify this iteration while the stage locks are held.
        # Restore its original files if the DB transaction/backup did not finish.
        root.rmdir()
        if had_root:
            archive.rename(root)
        if deleted_crem and isinstance(exc, RuntimeError):
            raise WorkspaceReconciliationPending(str(exc)) from exc
        raise
    print(f'Iteration {iteration}: cleared previous calculation files before generation; '
          f'preserved in {archive}.', flush=True)
    if deleted:
        print(f'Iteration {iteration}: backed up SQLite/WAL and reset orphaned selection, '
              'scaffold and generation progress; starting a fresh population.', flush=True)
    return archive
