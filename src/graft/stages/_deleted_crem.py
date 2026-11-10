"""Read-only retirement checks when both a CReM population and folder were deleted."""
import json
from pathlib import Path
import shlex
import subprocess
import time
import re

from graft import database, schrodinger_guard as guard


# Run on the configured Amber host. Retain every scratch tree and scientific
# result; only inspect its native status and execution locks.
AMBER_PROBE = r'''
import json, subprocess, sys
from pathlib import Path
request = json.load(sys.stdin)
owned = set(request['ids'])
proof = []
for root in sorted(Path(request['base']).glob('run-*')):
    source = root / 'input.json'
    if not source.is_file():
        raise RuntimeError('Amber scratch directory has no ownership manifest: ' + str(root))
    entries = json.loads(source.read_text())['entries']
    ids = {entry['id'] for entry in entries}
    if not (ids & owned):
        continue
    if not ids or not ids <= owned or root.is_symlink():
        raise RuntimeError('Ambiguous Amber scratch ownership: ' + str(root))
    journal = json.loads((root / 'slurm-submission.json').read_text())
    if journal.get('launching') or not str(journal.get('job_id', '')).isdigit():
        raise RuntimeError('Amber submission identity is uncertain: ' + str(root))
    def query(command):
        return json.loads(subprocess.check_output(command, text=True, stderr=subprocess.PIPE, timeout=60))
    native = query(['python3', str(root/'amber_slurm_control.py'), 'status',
                    str(journal['job_id']), str(len(entries))])
    if native['active']:
        raise RuntimeError('Amber job remains active: ' + str(journal['job_id']))
    snapshot = query(['python3', str(root/'amber_array.py'), 'collect', str(root)])
    if snapshot['active'] or len(snapshot['tasks']) != len(entries):
        raise RuntimeError('Amber task locks/evidence remain uncertain: ' + str(root))
    proof.append(dict(root=str(root), native=native, snapshot=snapshot))
for receipt in sorted(Path(request['base']).glob('.cleanup-run-*.json')):
    saved = json.loads(receipt.read_text())
    ids = set(saved['ids'])
    if not (ids & owned) or (Path(request['base'])/saved['run_id']).exists():
        continue
    if (receipt.is_symlink() or not ids <= owned or set(map(int, saved['successful'])) != ids
            or saved['base'] != request['base'] or not str(saved.get('job_id', '')).isdigit()):
        raise RuntimeError('Amber cleanup receipt is incomplete or foreign')
    checked = subprocess.run(['python3', '-', 'status', str(saved['job_id']), str(len(ids))],
                             input=request['status_source'], capture_output=True, text=True, check=True, timeout=60)
    native = json.loads(checked.stdout)
    if native['active']:
        raise RuntimeError('Retired Amber job is active or uncertain')
    proof.append(dict(receipt=str(receipt), request=saved, native=native))
print(json.dumps(proof))
'''


def _listed_ids(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    message = (result.stdout + result.stderr).strip()
    if result.returncode == 1 and message == 'No jobs matching your search criteria were found.':
        return []
    if result.returncode or result.stderr.strip():
        raise RuntimeError(f'Native job query failed for {command[command.index("--server")+1]}: '
                           f'{message or "no diagnostic"}')
    values = result.stdout.split()
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in values):
        raise RuntimeError('Native job query returned incomplete or invalid job identities')
    return values


def native_history(suite, since):
    """Query each registered server explicitly; an aggregate list can hide errors.

    Reinstalled servers can have no historical jobs. A fresh complete active
    snapshot is still required, including old/orphaned children outside the date
    filter. Authentication/unavailable servers must never look like empty queues.
    """
    executable = str(Path(suite)/'jsc')
    listed = subprocess.run([executable, 'cert', 'list'], check=True,
                            capture_output=True, text=True, timeout=30)
    servers = sorted(set(re.findall(r'^\s+(\S+:\d+)\s+\(user:', listed.stdout, re.M)))
    if listed.stderr.strip() or not servers:
        raise RuntimeError('Cannot enumerate registered native JobServers; recovery withheld')
    history, active = set(), set()
    for server in servers:
        base = [executable, 'list', '--server', server, '--id-only']
        active.update(_listed_ids(base))
        command = [*base, '--any-status', '--without-subjobs', '--with-deleted-jobs']
        if since is not None:
            command += ['--created-after', time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(since-86400))]
        history.update(_listed_ids(command))
    return dict(servers=servers, history_ids=sorted(history), active_ids=sorted(active),
                checked_at=time.time())


def _belongs(row, root):
    launch = row.get('spec', {}).get('launchParams', {}).get('launchDirectory')
    if launch and Path(launch).resolve().is_relative_to(root.resolve()):
        return True
    prefix = str(root) + '/'
    if prefix in row.get('commandLine', ''):
        return True
    # An orphaned child may launch in native scratch rather than the project.
    files = row.get('spec', {}).get('taskSpec', {}).get('input', {}).get('inputFiles', [])
    return any(prefix in entry.get('contentUri', '') for entry in files)


def scheduler_snapshot(args, root):
    """A reinstalled JobServer must not hide surviving native Slurm tasks."""
    if not getattr(args, 'use_slurm', False):
        return None
    from graft import amber_gbsa
    settings = args.amber_remote
    command = shlex.join(['squeue', '--me', '--noheader', '--format=%i|%j|%T|%Z'])
    response = amber_gbsa.ssh(settings, command, capture_output=True, text=True, timeout=60)
    if response.returncode or response.stderr.strip():
        raise RuntimeError('Cannot verify Slurm tasks after native history loss: ' +
                           (response.stderr or response.stdout).strip())
    rows = []
    for line in response.stdout.splitlines():
        values = line.split('|')
        if len(values) != 4:
            raise RuntimeError('Incomplete Slurm task ownership response')
        identity, name, state, directory = values
        native_name = name.lower().startswith(('glide', 'ligprep', 'prime', 'mmgbsa'))
        owned = directory.startswith('/') and Path(directory).resolve().is_relative_to(root.resolve())
        if native_name or owned:
            raise RuntimeError(f'Slurm job {identity} ({name}, {state}) remains after native history loss; '
                               'retirement/ownership must be reconciled')
        rows.append(dict(job_id=identity, name=name, state=state, directory=directory))
    return dict(host=settings['host'], tasks=rows, checked_at=time.time())


def reconcile(args, iteration, root):
    context = database.deleted_crem_recovery_context(args.db_path, iteration)
    stages = set(context['stages'])
    if 'fep' in stages:
        raise RuntimeError('FEP journals were deleted; explicit reconciliation required')
    md = {}
    if context['md'] is not None:
        from graft.stages._deleted_md import reconcile as reconcile_md
        md = reconcile_md(context['md'])
    elif stages & {'md-gbsa', 'md_gbsa'}:
        raise RuntimeError('MD selection was deleted; explicit remote reconciliation required')
    amber = reconcile_amber(args, context, stages)
    query = native_history(args.schrodinger, context['since'])
    identities = sorted(set(query['history_ids']) | set(query['active_ids']))
    native = []
    for identity in identities:
        if not identity.strip():
            continue
        rows = guard.job_details(args.schrodinger, identity.strip())
        belongs = any(_belongs(row, root) for row in rows)
        if not belongs:
            continue
        if any(row.get('status') not in guard.TERMINAL for row in rows):
            raise RuntimeError(f'Native job {identity} or its children remain live/uncertain')
        native.extend({key: row.get(key) for key in ('jobId', 'status', 'parentJobId', 'jobName')}
                      for row in rows)
    scheduler = scheduler_snapshot(args, root) if not native else None
    return dict(native=native, native_query=query, native_history_missing=not native,
                scheduler=scheduler, amber=amber, md=md)


def reconcile_amber(args, context, stages):
    amber = []
    if stages & {'amber-gbsa', 'amber-gbsa-recovery'}:
        if not getattr(args, 'use_slurm', False):
            raise RuntimeError('Deleted non-Slurm Amber journals require explicit remote reconciliation')
        from graft import amber_gbsa
        settings = args.amber_remote
        from graft import amber_slurm_control
        request = dict(base=settings['remote_work_dir'], ids=context['ids'],
                       status_source=Path(amber_slurm_control.__file__).read_text())
        response = amber_gbsa.ssh(settings, shlex.join(['python3', '-c', AMBER_PROBE]),
                                  input=json.dumps(request), capture_output=True, text=True, timeout=120)
        amber = json.loads(response.stdout)
        if not amber:
            raise RuntimeError('No surviving Amber ownership/task evidence; recovery withheld')
    return amber
