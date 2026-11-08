"""Read-only retirement checks when both a CReM population and folder were deleted."""
import json
from pathlib import Path
import shlex
import subprocess
import time

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
print(json.dumps(proof))
'''


def reconcile(args, iteration, root):
    context = database.deleted_crem_recovery_context(args.db_path, iteration)
    stages = set(context['stages'])
    if stages & {'md-gbsa', 'md_gbsa', 'fep'}:
        raise RuntimeError('MD/FEP journals were deleted; explicit remote reconciliation required')
    command = [str(Path(args.schrodinger)/'jsc'), 'list', '--any-status', '--id-only', '--without-subjobs']
    if context['since'] is not None:
        command += ['--created-after', time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(context['since']-86400))]
    listed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if listed.returncode:
        if (listed.stdout + listed.stderr).strip() != 'No jobs matching your search criteria were found.':
            listed.check_returncode()
        identities = []
    else:
        identities = listed.stdout.splitlines()
    native = []
    for identity in identities:
        if not identity.strip():
            continue
        rows = guard.job_details(args.schrodinger, identity.strip())
        belongs = any(
            (row.get('spec', {}).get('launchParams', {}).get('launchDirectory', '')+'/').startswith(str(root)+'/')
            or str(root)+'/' in row.get('commandLine', '') for row in rows)
        if not belongs:
            continue
        if any(row.get('status') not in guard.TERMINAL for row in rows):
            raise RuntimeError(f'Native job {identity} or its children remain live/uncertain')
        native.extend({key: row.get(key) for key in ('jobId', 'status', 'parentJobId', 'jobName')}
                      for row in rows)
    if stages & {'ligprep', 'glide', 'prime_gbsa', 'prime_gbsa-recovery'} and not native:
        raise RuntimeError('No native history found for the deleted calculation journals')
    amber = []
    if stages & {'amber-gbsa', 'amber-gbsa-recovery'}:
        if not getattr(args, 'use_slurm', False):
            raise RuntimeError('Deleted non-Slurm Amber journals require explicit remote reconciliation')
        from graft import amber_gbsa
        settings = args.amber_remote
        request = dict(base=settings['remote_work_dir'], ids=context['ids'])
        response = amber_gbsa.ssh(settings, shlex.join(['python3', '-c', AMBER_PROBE]),
                                  input=json.dumps(request), capture_output=True, text=True, timeout=120)
        amber = json.loads(response.stdout)
        if not amber:
            raise RuntimeError('No surviving Amber ownership/task evidence; recovery withheld')
    return dict(native=native, amber=amber)
