"""Verify retired MD work after its compound rows and local output were deleted."""
import json
from pathlib import Path
import shlex

from graft import md_gbsa


# This probe only reads ownership/journals/native state and briefly takes the
# existing controller locks. It never polls scientific helpers or starts jobs.
PROBE = r'''
from contextlib import ExitStack
import fcntl, hashlib, importlib.util, json, subprocess, sys
from pathlib import Path
request = json.load(sys.stdin)
selection = request['selection']
settings = selection['settings']
local = request['local']
remote = settings['local_compute'] if local else settings
root = Path(remote['root'])
if root.is_symlink() or not root.is_dir():
    raise RuntimeError('MD recovery root is missing or uncertain: ' + str(root))
if local:
    owner = dict(mode='adam_prime_v1', source_root=settings['root'], local=remote)
    if json.loads((root/'owner.json').read_text()) != owner:
        raise RuntimeError('Prime recovery ownership differs')
else:
    if json.loads((root/'ready.json').read_text()) != selection:
        raise RuntimeError('MD recovery ownership differs')
for name, digest in (remote.get('files', {}) if local else {'native.py': settings['helper_sha256']}).items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest() != digest:
        raise RuntimeError('Frozen MD helper differs: ' + name)
sys.path.insert(0, str(root))
if local:
    import md_gbsa_native as native
    spec = importlib.util.spec_from_file_location('graft_recovery_adapter', request['adapter'])
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    adapter.install(native)
else:
    spec = importlib.util.spec_from_file_location('graft_frozen_native', root/'native.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
ids = set(selection['ids'])
replicates = settings['replicates']
metadata, jobs, prime, recovered = {}, {}, [], []
with ExitStack() as stack:
    locked = set()
    def lock(path):
        if path in locked:
            return
        locked.add(path)
        stream = stack.enter_context(path.open('a'))
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock(root/'cleanup-run.lock')
    for path in root.rglob('controller.lock'):
        if path.is_symlink():
            raise RuntimeError('Controller lock is a symlink')
        lock(path)
    for folder in root.glob('CMPID_*'):
        if folder.is_symlink() or not folder.is_dir() or int(folder.name[6:]) not in ids:
            raise RuntimeError('Foreign MD compound directory')
        for replica in folder.glob('replica*'):
            if replica.is_symlink() or not replica.is_dir() or not 1 <= int(replica.name[7:]) <= replicates:
                raise RuntimeError('Foreign MD replica directory')
            lock(replica/'controller.lock')
    if not local:
        for cid in ids:
            if not (root/f'CMPID_{cid}').is_dir():
                receipt = json.loads((root/f'.cleanup-CMPID_{cid}.json').read_text())
                if receipt['selection'] != selection or receipt['id'] != cid:
                    raise RuntimeError('MD compound cleanup ownership differs')
    for path in root.rglob('*.json'):
        if path.is_symlink():
            raise RuntimeError('MD metadata is a symlink')
        value = json.loads(path.read_text())
        metadata[str(path.relative_to(root))] = value
        if path.name.endswith('.job.json'):
            if local and path.parent.name == 'md':
                continue  # Frozen MD input copy; verified on the Slurm host.
            if not local and path.parent.name == 'prime' and settings.get('local_compute'):
                if not value.get('job_id'):
                    raise RuntimeError('Copied Prime launch identity is uncertain')
                prime.append(value)
                continue
            if not value.get('job_id'):
                if local or value.get('client_exit') in (None, 0):
                    raise RuntimeError('MD native launch identity is uncertain')
                listed = subprocess.run([str(Path(remote['suite'])/'jsc'), 'list', '--any-status',
                    '--id-only', '--without-subjobs', '--launch-dir', str(path.parent)],
                    check=True, capture_output=True, text=True, timeout=30)
                identities = listed.stdout.split()
                if not identities:
                    raise RuntimeError('Failed MD launch has no authoritative native history')
                recovered.append(dict(path=str(path.relative_to(root)), job_ids=identities))
                for identity in identities:
                    jobs[identity] = dict(value, job_id=identity)
                continue
            jobs[value['job_id']] = value
    for value in request.get('prime_journals', []):
        jobs[value['job_id']] = value
    evidence = []
    for identity, record in jobs.items():
        suite = (Path(record['command'][0]).parent if local and record.get('command')
                 else Path(remote['suite']))
        rows = native.native_rows(suite, identity)
        if not any(row['jobId'] == identity for row in rows) or any(row.get('status') not in native.TERMINAL for row in rows):
            raise RuntimeError('MD native tree is live or uncertain: ' + identity)
        evidence.extend({key: row.get(key) for key in ('jobId','status','parentJobId')} for row in rows)
print(json.dumps(dict(root=str(root), metadata=metadata, native=evidence,
                     prime_journals=prime, recovered_launches=recovered)))
'''


def reconcile(selection):
    settings = selection['settings']
    proofs = []
    prime = []
    for local in (False, True) if settings.get('local_compute') else (False,):
        remote = settings['local_compute'] if local else settings
        jobs = md_gbsa.response(md_gbsa.remote_call(remote, 'jobs', remote['root']))
        if jobs:
            raise RuntimeError('MD/Prime jobs are active or uncertain; deletion recovery withheld')
        request = dict(selection=selection, local=local, prime_journals=prime)
        if local:
            from graft.md_gbsa_local import control_helper
            request['adapter'] = str(control_helper(remote))
        result = json.loads(md_gbsa.ssh(remote['ssh_host'], shlex.join([
            remote['suite']+'/run', 'python3', '-c', PROBE]), input=json.dumps(request)))
        prime = result['prime_journals']
        proofs.append(result)
    return dict(selection=selection, roots=proofs)
