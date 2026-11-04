"""Serialize submissions and fence live Glide/Prime jobs using JobServer."""
import fcntl
import json
import re
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

TERMINAL = {'DONE', 'FAILED', 'CANCELED', 'STOPPED'}


class SubmissionBlocked(RuntimeError):
    pass


def verify_submission_host(suite, host):
    """Read-only compatibility preflight before making a new native launch intent."""
    executable = str(Path(suite)/'jsc')
    result = subprocess.run([executable,'hosts','list','--verbose'], check=True,
                            capture_output=True,text=True,timeout=30)
    names = {entry.split(':',1)[0] for entry in host.split()}
    headers = list(re.finditer(r'^  (\S+:\d+)\s*$',result.stdout,re.M))
    found = set()
    for index, header in enumerate(headers):
        body = result.stdout[header.end():headers[index+1].start() if index+1<len(headers) else len(result.stdout)]
        matching = {name for name in names if re.search(r'^\s+(?:Name:\s*)?'+re.escape(name)+r'\s*$',body,re.M)}
        if not matching:
            continue
        found.update(matching)
        server = header[1]
        info = subprocess.run([executable,'server-info',server],check=True,
                              capture_output=True,text=True,timeout=30).stdout
        versions = {key:tuple(map(int,value.split('.'))) for key,value in
                    re.findall(r'(Server|Client) API version:\s+v([0-9.]+)',info)}
        if set(versions) != {'Server','Client'}:
            raise SubmissionBlocked(f'Cannot verify native API compatibility for {server}; no job submitted')
        if versions['Client'] > versions['Server']:
            version = lambda key: '.'.join(map(str,versions[key]))
            raise SubmissionBlocked(f"Host {host}: JobServer {server} API {version('Server')} is older "
                                    f"than client API {version('Client')}; upgrade the server before new submissions")
    if found != names:
        raise SubmissionBlocked(f'Native hosts not available through registered JobServers: {sorted(names-found)}')


def job_details(suite, job_id, directory=None):
    result = subprocess.run([str(Path(suite) / 'jsc'), 'info', '--json', job_id],
                            cwd=directory, check=True, capture_output=True, text=True, timeout=30)
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not any(row.get('jobId') == job_id for row in rows):
        raise RuntimeError(f'JobServer returned no parent {job_id}')
    return rows


def job_kind(job):
    name = job.get('jobName', '').lower()
    command = job.get('commandLine', '').lower()
    program = job.get('spec', {}).get('taskSpec', {}).get('programName', '').lower()
    if ('prime_gbsa' in name or 'mmgbsa' in name or 'prime_mmgbsa' in command
            or 'prime_gbsa' in program or 'mmgbsa' in program):
        return 'prime_gbsa'
    if name.startswith('glide') or '/glide ' in command or 'glide_wrap.py' in command or program == 'glide':
        return 'glide'
    return None


def _local_active_jobs(suite):
    result = subprocess.run([str(Path(suite) / 'jsc'), 'list', '--id-only'],
                            capture_output=True, text=True, timeout=30)
    if getattr(result, 'returncode', 0):
        message = (result.stdout + result.stderr).strip()
        if result.returncode == 1 and message == 'No jobs matching your search criteria were found.':
            return []
        result.check_returncode()
    rows = {}
    inspected = set()
    for job_id in result.stdout.splitlines():
        job_id = job_id.strip()
        if not job_id or job_id in inspected:
            continue
        for job in job_details(suite, job_id):
            inspected.add(job['jobId'])
            if job.get('status') not in TERMINAL:
                rows[job['jobId']] = job
    return list(rows.values())



def _md_registry():
    return Path.home() / '.cache' / 'graft' / 'md-remotes.json'


def register_md_remote(settings):
    # Same submission mutex as Glide/Prime. Registrations remain durable so a
    # controller crash cannot hide active or uncertain Slurm jobs.
    path = Path(tempfile.gettempdir()) / f'graft-schrodinger-submit-{os.getuid()}.lock'
    with path.open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        registry = _md_registry()
        registry.parent.mkdir(parents=True,exist_ok=True)
        saved = json.loads(registry.read_text()) if registry.exists() else {}
        for remote in (settings, settings.get('local_compute')):
            if remote:
                saved[remote['ssh_host']+'|'+remote['suite']+'|'+remote['root']] = {
                    key:remote[key] for key in ('ssh_host','suite','helper','root')}
        from graft.md_gbsa_native import save
        save(registry,saved)



def unregister_md_remote(settings):
    path = Path(tempfile.gettempdir()) / f'graft-schrodinger-submit-{os.getuid()}.lock'
    with path.open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        registry = _md_registry()
        if registry.exists():
            saved=json.loads(registry.read_text())
            for remote in (settings, settings.get('local_compute')):
                if remote:
                    saved.pop(remote['ssh_host']+'|'+remote['suite']+'|'+remote['root'],None)
            from graft.md_gbsa_native import save
            save(registry,saved)

def active_jobs(suite):
    rows = _local_active_jobs(suite)
    registry = _md_registry()
    if registry.exists():
        from graft.md_gbsa import remote_call, response
        for settings in json.loads(registry.read_text()).values():
            for job in response(remote_call(settings,'jobs',settings['root'])):
                rows.append(dict(job,graft_md_remote=True,
                    graft_md_local_compute=settings['helper'].endswith('/md_gbsa_local_native.py'),
                    graft_prime_local_compute=settings['helper'].endswith('/prime_gbsa_local_native.py')))
    return rows

def belongs_to_directory(job, directory):
    launch = job.get('spec', {}).get('launchParams', {}).get('launchDirectory')
    if launch:
        return Path(launch).resolve() == Path(directory).resolve()
    return str(Path(directory).resolve()) + '/' in job.get('commandLine', '')


@contextmanager
def submission_guard(suite, stage, *, md_controller=False):
    # Shared across projects for this user, not held while calculations run.
    path = Path(tempfile.gettempdir()) / f'graft-schrodinger-submit-{os.getuid()}.lock'
    with path.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SubmissionBlocked('Another Schrödinger submission check is in progress') from exc
        try:
            opposite = 'glide' if stage == 'prime_gbsa' else 'prime_gbsa'
            try:
                jobs = active_jobs(suite)
            except Exception as exc:
                raise SubmissionBlocked(f'Cannot verify external jobs; submissions withheld: {exc}') from exc
            live = [j for j in jobs if job_kind(j) == opposite or
                    (stage == 'prime_gbsa' and not md_controller and
                     j.get('graft_md_remote') and job_kind(j) == 'prime_gbsa')]
            if live:
                raise SubmissionBlocked(f"{job_kind(live[0])} still active in JobServer: " +
                                        ', '.join(j['jobId'] for j in live))
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
